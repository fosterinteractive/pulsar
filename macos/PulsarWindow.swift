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
    private var warp: NativeWarp?

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
        config.userContentController.addUserScript(WKUserScript(source: "window.PULSAR_NATIVE_WARP = 1;",
                                                                injectionTime: .atDocumentStart, forMainFrameOnly: true))
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
        let root = NSView()
        root.wantsLayer = true
        web.autoresizingMask = [.width, .height]
        root.addSubview(web)
        window.contentView = root
        web.frame = root.bounds
        warp = NativeWarp(web: web, host: root)
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
            item("About Pulsar", #selector(showAbout), target: self),
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

    /// The About panel with the version that is really running (the code updates itself, the app does not), its
    /// pulsar's name, and one line about the name.
    @objc private func showAbout() {
        let bundled = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let show = { (version: String) in
            let line = NSAttributedString(string: "Pulsars are among the most precise clocks in the universe, hence the name.",
                attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor])
            NSApp.orderFrontStandardAboutPanel(options: [.applicationVersion: version, .version: version, .credits: line])
            NSApp.activate(ignoringOtherApps: true)
        }
        guard let origin = origin, let url = URL(string: "api/version", relativeTo: origin) else { show(bundled); return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: request) { data, _, _ in
            var version = bundled
            if let data = data, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let running = json["version"] as? String {
                let name = json["codename"] as? String ?? ""
                version = name.isEmpty ? running : "\(running) · \(name)"
            }
            DispatchQueue.main.async { show(version) }
        }.resume()
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
        case "warp":   // {to: 0..1, instant, dark}: the page's warp, drawn here on the graphics card
            warp?.to(CGFloat((body["to"] as? NSNumber)?.doubleValue ?? 0), instant: body["instant"] as? Bool ?? false,
                     dark: body["dark"] as? Bool ?? true)
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
        if ProcessInfo.processInfo.environment["PULSAR_TEST_WARP"] != nil { testWarp(at); return }
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

    /// The native warp, photographed from the window itself (an app may capture its own window without permission):
    /// held at full strength, letting go, and after; warp.json says whether the overlay went away.
    private func testWarp(_ at: URL) {
        let shoot = { (name: String) in
            if let img = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(self.window.windowNumber),
                                                 [.boundsIgnoreFraming, .bestResolution]),
               let png = NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:]) {
                try? png.write(to: at.appendingPathComponent(name))
            }
        }
        let after = { (s: Double, f: @escaping () -> Void) in DispatchQueue.main.asyncAfter(deadline: .now() + s, execute: f) }
        after(4) {
            self.web.evaluateJavaScript("NATIVE_WARP") { native, _ in
                self.web.evaluateJavaScript("warpTo(1)")
                after(1.0) { shoot("warp-held.png")
                    self.web.evaluateJavaScript("warpRelease()")
                    after(0.12) { shoot("warp-out.png")
                        after(1.2) { shoot("warp-after.png")
                            let layers = self.window.contentView?.subviews.count ?? -1
                            let json = "{\"native\": \(native as? Bool ?? false), \"viewsAfter\": \(layers)}"
                            try? json.write(to: at.appendingPathComponent("warp.json"), atomically: true, encoding: .utf8)
                            NSApp.terminate(nil)
                        }
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

/// The warp in the window. A picture of the page is bent by Core Image's hole distortion (light pushed round a point,
/// as round a black hole), with the core, a ring of light and the flash drawn as Core Animation layers. All of it is
/// animated by Core Animation on the graphics card: no frame is computed on the main thread, so it stays smooth on
/// any Mac that runs Pulsar while the page underneath keeps loading. It sits over the page, so it holds through the
/// reload into a new version, and lets go only when the new page asks (or after ten seconds, whatever happens).
final class NativeWarp {
    private weak var web: WKWebView?
    private weak var host: NSView?
    private var overlay: NSView?
    private var picture: CALayer?
    private var core: CAGradientLayer?
    private var level: CGFloat = 0
    private var snapping = false
    private var queued: (CGFloat, Bool, Bool)?
    private var safety: DispatchWorkItem?
    private var pullEnds: CFTimeInterval = 0  // when the pull in is all the way in
    private var letGo = false                 // the page let go before the pull was in: let go once it is
    private let inEase = CAMediaTimingFunction(controlPoints: 0.55, 0, 0.9, 0.45)  // slow at first, as gravity pulls
    private let outEase = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.3, 1)

    init(web: WKWebView, host: NSView) { self.web = web; self.host = host }

    func to(_ target: CGFloat, instant: Bool, dark: Bool) {
        if target <= 0 { release(); return }
        letGo = false
        guard overlay == nil else { pull(to: target, instant: instant); return }
        if snapping { queued = (target, instant, dark); return }
        snapping = true
        let shot = WKSnapshotConfiguration()
        shot.afterScreenUpdates = false
        web?.takeSnapshot(with: shot) { [weak self] image, _ in
            guard let self = self else { return }
            self.snapping = false
            guard let image = image, let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
            self.build(cg, dark: dark)
            let (t, i, _) = self.queued ?? (target, instant, dark)
            self.queued = nil
            self.pull(to: t, instant: i)
            if self.letGo { self.release() }  // the page let go while the picture was being taken
        }
    }

    private var size: CGSize { host?.bounds.size ?? .zero }
    private var centre: CGPoint { CGPoint(x: size.width / 2, y: size.height * 0.55) }  // 45% from the top
    private var radius: CGFloat { min(size.width, size.height) * 0.15 }

    private func build(_ image: CGImage, dark: Bool) {
        guard let host = host else { return }
        let view = NSView(frame: host.bounds)
        view.wantsLayer = true
        view.layerUsesCoreImageFilters = true
        view.autoresizingMask = [.width, .height]
        let root = view.layer!
        let scale = host.window?.backingScaleFactor ?? 2

        let pic = CALayer()
        pic.frame = view.bounds
        pic.contents = image
        pic.contentsGravity = .resize
        pic.contentsScale = scale
        pic.anchorPoint = CGPoint(x: 0.5, y: 0.55)
        pic.position = centre
        let hole = CIFilter(name: "CIHoleDistortion")!
        hole.name = "hole"
        hole.setValue(CIVector(x: centre.x * scale, y: centre.y * scale), forKey: kCIInputCenterKey)
        hole.setValue(0.01, forKey: kCIInputRadiusKey)
        pic.filters = [hole]
        root.addSublayer(pic)

        let side = radius * 2.6
        let ring = CAGradientLayer()
        ring.type = .radial
        ring.frame = CGRect(x: centre.x - side / 2, y: centre.y - side / 2, width: side, height: side)
        ring.startPoint = CGPoint(x: 0.5, y: 0.5)
        ring.endPoint = CGPoint(x: 1, y: 1)
        let c: CGFloat = dark ? 0 : 1  // a dark hole on the dark theme, a bright one on the light theme
        ring.colors = [NSColor(white: c, alpha: 0.94), NSColor(white: c, alpha: 0.94), NSColor(white: c, alpha: 0.5),
                       NSColor(white: c, alpha: 0), NSColor(red: 0.75, green: 0.67, blue: 1, alpha: 0),
                       NSColor(red: 0.75, green: 0.67, blue: 1, alpha: 0.38), NSColor(red: 0.47, green: 0.86, blue: 1, alpha: 0.16),
                       NSColor(red: 0.47, green: 0.86, blue: 1, alpha: 0)].map { $0.cgColor }
        ring.locations = [0, 0.32, 0.48, 0.62, 0.69, 0.77, 0.83, 0.96]
        ring.opacity = 0
        root.addSublayer(ring)

        host.addSubview(view)
        overlay = view; picture = pic; core = ring; level = 0
    }

    /// pull in to `target` and hold there, the core breathing a little
    private func pull(to target: CGFloat, instant: Bool) {
        guard let pic = picture, let ring = core else { return }
        let from = level, duration = instant ? 0 : max(0.01, 0.52 * Double(abs(target - from)))
        level = target
        pullEnds = CACurrentMediaTime() + duration
        animate(pic, "filters.hole.inputRadius", from: holeRadius(from), to: holeRadius(target), duration, inEase)
        animate(pic, "transform.scale", from: 1 - 0.06 * from, to: 1 - 0.06 * target, duration, inEase)
        animate(ring, "opacity", from: min(1, 1.4 * from), to: min(1, 1.4 * target), duration, inEase)
        animate(ring, "transform.scale", from: 0.5 + 0.5 * from, to: 0.5 + 0.5 * target, duration, inEase)
        let breathe = CABasicAnimation(keyPath: "transform.scale")
        breathe.fromValue = 0.5 + 0.5 * target; breathe.toValue = (0.5 + 0.5 * target) * 1.05
        breathe.duration = 0.48; breathe.autoreverses = true; breathe.repeatCount = .infinity
        breathe.beginTime = CACurrentMediaTime() + duration
        breathe.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        ring.add(breathe, forKey: "breathe")
        safety?.cancel()
        let item = DispatchWorkItem { [weak self] in self?.release() }  // never left over the page
        safety = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 10, execute: item)
    }

    /// let go: the lens closes, the picture springs just past its size and back and fades into the live page. The
    /// page times its warp from when it asks; the picture is taken first, so a letting go can come before the pull
    /// is in (it came at about 60% of it): then it waits for it, and one during the picture waits for the pull.
    func release() {
        if snapping { letGo = true; return }
        let wait = pullEnds - CACurrentMediaTime()
        if overlay != nil && wait > 0.01 {
            letGo = true
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                guard let self = self, self.letGo else { return }  // a newer pull took over meanwhile
                self.release()
            }
            return
        }
        letGo = false
        safety?.cancel()
        guard let view = overlay, let pic = picture, let ring = core else { queued = nil; return }
        let from = level, root = view.layer!
        level = 0
        ring.removeAnimation(forKey: "breathe")
        CATransaction.begin()
        CATransaction.setCompletionBlock { [weak self] in
            view.removeFromSuperview()
            if self?.overlay === view { self?.overlay = nil; self?.picture = nil; self?.core = nil }
        }
        animate(pic, "filters.hole.inputRadius", from: holeRadius(from), to: holeRadius(0), 0.29, outEase)
        let spring = CAKeyframeAnimation(keyPath: "transform.scale")
        spring.values = [1 - 0.06 * from, 1.012, 1]; spring.keyTimes = [0, 0.45, 1]
        spring.duration = 0.52; spring.timingFunctions = [outEase, outEase]
        pic.add(spring, forKey: "spring"); pic.setValue(1, forKeyPath: "transform.scale")
        animate(ring, "opacity", from: min(1, 1.4 * from), to: 0, 0.29, outEase)
        let fade = CAKeyframeAnimation(keyPath: "opacity")  // into the live page, which may have changed meanwhile
        fade.values = [1, 1, 0]; fade.keyTimes = [0, 0.5, 1]; fade.duration = 0.6
        root.add(fade, forKey: "fade"); root.opacity = 0
        flash(in: root)
        CATransaction.commit()
    }

    private func holeRadius(_ level: CGFloat) -> CGFloat { max(0.01, radius * 0.62 * level) }

    private func flash(in root: CALayer) {
        let side = min(size.width, size.height) * 0.9
        let f = CAGradientLayer()
        f.type = .radial
        f.frame = CGRect(x: centre.x - side / 2, y: centre.y - side / 2, width: side, height: side)
        f.startPoint = CGPoint(x: 0.5, y: 0.5); f.endPoint = CGPoint(x: 1, y: 1)
        f.colors = [NSColor(white: 1, alpha: 0.95), NSColor(white: 1, alpha: 0.55), NSColor(red: 0.47, green: 0.86, blue: 1, alpha: 0.32),
                    NSColor(red: 0.63, green: 0.43, blue: 1, alpha: 0.16), NSColor(red: 1, green: 0.35, blue: 0.78, alpha: 0.06),
                    NSColor(white: 1, alpha: 0)].map { $0.cgColor }
        f.locations = [0, 0.08, 0.24, 0.48, 0.68, 0.92]
        f.opacity = 0
        root.addSublayer(f)
        let o = CAKeyframeAnimation(keyPath: "opacity")
        o.values = [0, 1, 0]; o.keyTimes = [0, 0.18, 1]; o.duration = 0.7
        let g = CABasicAnimation(keyPath: "transform.scale")
        g.fromValue = 0.25; g.toValue = 2.6; g.duration = 0.7; g.timingFunction = outEase
        f.add(o, forKey: "o"); f.add(g, forKey: "g")
    }

    private func animate(_ layer: CALayer, _ path: String, from: CGFloat, to: CGFloat, _ duration: Double,
                         _ ease: CAMediaTimingFunction) {
        let a = CABasicAnimation(keyPath: path)
        a.fromValue = from; a.toValue = to; a.duration = max(duration, 0.0001); a.timingFunction = ease
        layer.add(a, forKey: path)
        layer.setValue(to, forKeyPath: path)  // the end state stays when the animation is done
    }
}

let app = NSApplication.shared
let pulsar = PulsarWindow()
app.delegate = pulsar
app.run()
