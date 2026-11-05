// Pulsar.app's window: Pulsar's page in a window of its own instead of a browser tab.
//
// This is the app's executable (build_app.sh compiles it to Pulsar.app/Contents/MacOS/Pulsar for Apple Silicon and
// Intel). It runs the launcher, Contents/Resources/launcher.sh, with PULSAR_WINDOW=1: the launcher installs or
// upgrades the code, starts the server and prints "PULSAR_URL <address with a one-time sign-in code>" instead of
// opening a browser. The window loads that address, so it is signed in at once.
//
//   - Only Pulsar's own address (http://127.0.0.1:<port>) opens in the window. A link elsewhere that the person
//     clicks opens in their browser; anything else is refused.
//   - Downloads (CSV, the error log) ask where to save; "Print or save as PDF" opens the print dialog.
//   - Closing the window quits the app, and quitting stops the server this app started (launcher.sh --stop). A
//     server started some other way, such as python3 pulsar.py in a git checkout, keeps running.
//   - A Mac whose Safari is older than 16.2 cannot draw the page: Pulsar then opens in the browser instead.
//
// Built with -D PULSAR_TEST, it also takes a picture, a download and a PDF by itself (see the end of this file).

import Cocoa
@preconcurrency import WebKit

final class PulsarWindow: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
    WKScriptMessageHandler, WKDownloadDelegate {
    private var window: NSWindow!
    private var web: WKWebView!
    private var origin: URL?           // http://127.0.0.1:<port>/ once the launcher has said where Pulsar runs
    private var starting = false
    private var keepServer = false     // true when the page was handed to the browser: it still needs the server
    private var stopped = false
    private let launcher = Bundle.main.url(forResource: "launcher", withExtension: "sh")

    // MARK: start

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        start()
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Runs the launcher in the background and loads the address it prints. The launcher shows its own dialogs
    /// when something is wrong (no developer tools, the port taken, a version that does not start).
    private func start() {
        guard !starting else { return }
        starting = true
        showMessage("Starting Pulsar…", button: nil)
        DispatchQueue.global(qos: .userInitiated).async {
            let url = self.runLauncher(["--window"])
            DispatchQueue.main.async {
                self.starting = false
                guard let url = url, let base = URL(string: "/", relativeTo: url)?.absoluteURL else {
                    NSApp.terminate(nil)   // the launcher has already said why, or the person chose "Not now"
                    return
                }
                self.origin = base
                self.loadWhenSupported(url)
            }
        }
    }

    /// Runs launcher.sh with the given arguments and returns the address it printed, if any.
    @discardableResult
    private func runLauncher(_ args: [String], timeout: TimeInterval = 300) -> URL? {
        guard let launcher = launcher else { return nil }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [launcher.path] + args
        let out = Pipe()
        task.standardOutput = out
        task.standardError = FileHandle.nullDevice
        task.standardInput = FileHandle.nullDevice
        do { try task.run() } catch { return nil }
        let done = DispatchSemaphore(value: 0)
        var data = Data()
        DispatchQueue.global().async {
            data = out.fileHandleForReading.readDataToEndOfFile()
            done.signal()
        }
        if done.wait(timeout: .now() + timeout) == .timedOut { task.terminate(); return nil }
        task.waitUntilExit()
        for line in String(decoding: data, as: UTF8.self).split(separator: "\n") where line.hasPrefix("PULSAR_URL ") {
            if let url = URL(string: String(line.dropFirst("PULSAR_URL ".count))), url.scheme == "http",
               url.host == "127.0.0.1" {
                return url
            }
        }
        return nil
    }

    /// The page needs Safari 16.2 or newer (color-mix and container queries). The window uses the Mac's own
    /// Safari engine, so on an older one Pulsar opens in the default browser instead, which may be newer.
    private func loadWhenSupported(_ url: URL) {
        let test = "CSS.supports('color', 'color-mix(in srgb, red, blue)') && CSS.supports('container-type', 'inline-size')"
        web.evaluateJavaScript(test) { result, _ in
            if (result as? Bool) == true {
                self.web.load(URLRequest(url: url))
                return
            }
            self.keepServer = true
            let alert = NSAlert()
            alert.messageText = "Pulsar opens in your browser"
            alert.informativeText = "This Mac's Safari is older than version 16.2, so the Pulsar window cannot show the page. "
                + "Update Safari in System Settings > General > Software Update to use the window."
            alert.runModal()
            NSWorkspace.shared.open(url)
            NSApp.terminate(nil)
        }
    }

    // MARK: window and menus

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()   // keeps the session token and the page's choices between launches
        config.userContentController.add(self, name: "pulsar")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsMagnification = true
        web.underPageBackgroundColor = .windowBackgroundColor

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(width: min(1320, screen.width - 80), height: min(880, screen.height - 60))
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Pulsar"
        window.minSize = NSSize(width: 640, height: 480)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("Pulsar")   // the next launch opens where this one was left
        window.makeKeyAndOrderFront(nil)
    }

    private func buildMenu() {
        let main = NSMenu()
        func menu(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem()
            holder.submenu = NSMenu(title: title)
            items.forEach { holder.submenu!.addItem($0) }
            main.addItem(holder)
        }
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command,
                  target: AnyObject? = nil) -> NSMenuItem {
            let it = NSMenuItem(title: title, action: action, keyEquivalent: key)
            it.keyEquivalentModifierMask = mods
            it.target = target
            return it
        }
        menu("Pulsar", [
            item("About Pulsar", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Settings…", #selector(openSettings), ",", target: self),
            .separator(),
            item("Hide Pulsar", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit Pulsar", #selector(NSApplication.terminate(_:)), "q"),
        ])
        menu("Edit", [   // without these, Cmd+C and Cmd+V do nothing in the page's fields
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        menu("View", [
            item("Reload", #selector(reload), "r", target: self),
            .separator(),
            item("Actual Size", #selector(zoomReset), "0", target: self),
            item("Zoom In", #selector(zoomIn), "=", target: self),
            item("Zoom Out", #selector(zoomOut), "-", target: self),
        ])
        let windowMenu: [NSMenuItem] = [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
            .separator(),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
        ]
        menu("Window", windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = main.items.last?.submenu
    }

    @objc private func openSettings() {
        if isPulsar(web.url) { web.evaluateJavaScript("typeof openSettings === 'function' && openSettings()") }
    }
    @objc private func reload() { if isPulsar(web.url) { web.reload() } else { start() } }
    @objc private func zoomReset() { web.pageZoom = 1 }
    @objc private func zoomIn() { web.pageZoom = min(2, web.pageZoom + 0.1) }
    @objc private func zoomOut() { web.pageZoom = max(0.5, web.pageZoom - 0.1) }

    /// A simple page of the window's own: "Starting Pulsar…", or what went wrong with a button to try again.
    private func showMessage(_ text: String, button: String?) {
        let esc = { (s: String) in s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;") }
        let action = button.map { "<button onclick=\"webkit.messageHandlers.pulsar.postMessage({action:'start'})\">\(esc($0))</button>" } ?? ""
        web.loadHTMLString("""
            <!doctype html><meta charset="utf-8"><style>
            :root { color-scheme: light dark; } html, body { height: 100%; margin: 0; }
            body { display: flex; flex-direction: column; gap: 14px; align-items: center; justify-content: center;
                   font: 14px -apple-system, system-ui, sans-serif; color: CanvasText; background: Canvas; }
            p { margin: 0; opacity: .75; } button { font: inherit; padding: 5px 14px; }
            </style><p>\(esc(text))</p>\(action)
            """, baseURL: nil)
    }

    // MARK: quitting

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    /// Stops the server this app started, unless the page went to the browser. At most a few seconds.
    func applicationShouldTerminate(_ app: NSApplication) -> NSApplication.TerminateReply {
        guard origin != nil, !keepServer, !stopped else { return .terminateNow }
        stopped = true
        window.orderOut(nil)
        DispatchQueue.global().async {
            self.runLauncher(["--stop"], timeout: 6)
            // Not DispatchQueue.main: while AppKit waits for this answer it runs the modal-panel run loop mode, and
            // when quitting was asked from a block on the main queue, that queue cannot run another block until then.
            self.performSelector(onMainThread: #selector(self.replyTerminate), with: nil, waitUntilDone: false,
                                 modes: [RunLoop.Mode.default.rawValue, RunLoop.Mode.modalPanel.rawValue])
        }
        return .terminateLater
    }

    @objc private func replyTerminate() { NSApp.reply(toApplicationShouldTerminate: true) }

    // MARK: what the window may open

    private func isPulsar(_ url: URL?) -> Bool {
        guard let url = url, let origin = origin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        let url = action.request.url
        if isPulsar(url) || url?.scheme == "blob" || (url?.absoluteString == "about:blank" && action.targetFrame?.isMainFrame == true) {
            decisionHandler(.allow)
            return
        }
        // A link elsewhere opens in the browser, only when the person clicked it in the page itself.
        if let url = url, ["http", "https", "mailto"].contains(url.scheme ?? ""), action.navigationType == .linkActivated,
           action.sourceFrame.isMainFrame {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    /// window.open and target="_blank": never a second window; a link elsewhere goes to the browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, ["http", "https", "mailto"].contains(url.scheme ?? ""), !isPulsar(url) {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    /// The server is not answering (it stopped, or the Mac slept through an update): offer to start it again.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code == NSURLErrorCancelled || code == 102 { return }   // 102: a navigation that became a download
        showMessage("Pulsar is not running.", button: "Start Pulsar")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { reload() }

    // MARK: messages from the page

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        switch action {
        case "print": printPage()
        case "signin", "start": start()   // a new one-time code, from the launcher, as at launch
        default: break
        }
    }

    private func printPage() {
        let info = (NSPrintInfo.shared.copy() as? NSPrintInfo) ?? NSPrintInfo()
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isHorizontallyCentered = false
        info.isVerticallyCentered = false
        (info.leftMargin, info.rightMargin, info.topMargin, info.bottomMargin) = (28, 28, 32, 32)   // as in a browser
        let op = web.printOperation(with: info)
        op.view?.frame = web.bounds   // without a frame WebKit prints blank pages
        op.jobTitle = web.title ?? "Pulsar report"
        op.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    // MARK: alerts and confirmations from the page (none today; without these they would do nothing)

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // MARK: downloads

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        #if PULSAR_TEST
        if let dir = ProcessInfo.processInfo.environment["PULSAR_TEST_DIR"] {
            let url = URL(fileURLWithPath: dir).appendingPathComponent(suggestedFilename)
            try? FileManager.default.removeItem(at: url)
            completionHandler(url)
            return
        }
        #endif
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.beginSheetModal(for: window) { choice in
            guard choice == .OK, let url = panel.url else { completionHandler(nil); return }
            try? FileManager.default.removeItem(at: url)   // the panel has asked about replacing it
            completionHandler(url)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        let alert = NSAlert()
        alert.messageText = "The download failed"
        alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window)
    }

    // MARK: test build only

    #if PULSAR_TEST
    /// With PULSAR_TEST_DIR set: once Pulsar's page has loaded, write what the page says about itself (page.json),
    /// a picture of the window (window.png), the error log through the download path, the report printed to a PDF
    /// (report.pdf), then quit (which stops the server).
    private var tested = false

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let dir = ProcessInfo.processInfo.environment["PULSAR_TEST_DIR"], isPulsar(web.url), !tested else { return }
        tested = true
        let at = URL(fileURLWithPath: dir)
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            let probe = """
                JSON.stringify({signedIn: !!localStorage.getItem('pulsar-session'), address: location.href,
                  signInShown: document.querySelector('#signin').open, title: document.title,
                  supports: CSS.supports('color', 'color-mix(in srgb, red, blue)'),
                  app: !!(window.webkit && webkit.messageHandlers && webkit.messageHandlers.pulsar)})
                """
            self.web.evaluateJavaScript(probe) { result, _ in
                try? ((result as? String) ?? "{}").write(to: at.appendingPathComponent("page.json"), atomically: true, encoding: .utf8)
                self.web.takeSnapshot(with: nil) { image, _ in
                    if let tiff = image?.tiffRepresentation, let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                        try? png.write(to: at.appendingPathComponent("window.png"))
                    }
                    self.web.evaluateJavaScript("downloadFromAPI('/api/logs/download?days=1', 'pulsar-log.txt'); setView('report')")
                    DispatchQueue.main.asyncAfter(deadline: .now() + 6) {
                        let info = NSPrintInfo()
                        info.horizontalPagination = .fit
                        (info.leftMargin, info.rightMargin, info.topMargin, info.bottomMargin) = (28, 28, 32, 32)
                        info.jobDisposition = .save
                        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = at.appendingPathComponent("report.pdf")
                        let op = self.web.printOperation(with: info)
                        op.view?.frame = self.web.bounds
                        op.showsPrintPanel = false
                        op.showsProgressPanel = false
                        op.runModal(for: self.window, delegate: self, didRun: #selector(self.printed), contextInfo: nil)
                    }
                }
            }
        }
    }

    @objc private func printed(_ op: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApp.terminate(nil) }
    }
    #endif
}

let app = NSApplication.shared
let pulsar = PulsarWindow()
app.delegate = pulsar
app.run()
