// Pulsar.app's window: Pulsar's page in a window of its own instead of a browser tab.
//
// This is the app's executable (build_app.sh compiles it to Pulsar.app/Contents/MacOS/Pulsar for Apple Silicon and
// Intel). It runs the launcher, Contents/Resources/launcher.sh, with --window: the launcher installs or
// upgrades the code, starts the server and prints "PULSAR_URL <address with a one-time sign-in code>" instead of
// opening a browser. The window loads that address, so it is signed in at once.
//
//   - Only Pulsar's own address (http://127.0.0.1:<port>) opens in the window. A link elsewhere that the person
//     clicks opens in their browser; anything else is refused. Only Pulsar's own page may message the window.
//   - Every load of the page goes through the launcher: at start, View > Reload Pulsar, a new sign-in, and the page's
//     own reload into a new version. The launcher checks that the server on the port holds this user's key before it
//     gives the address, so the window never loads whatever else may have taken the port while Pulsar restarted. The
//     day, view and range shown are carried over. Right-click never offers WebKit's own Reload, which would not.
//   - The launcher's dialogs are sheets on this window, and what it is doing shows under a spinner while it starts.
//   - The menus act on the page through web/js/native.js (pulsarNative), which tells the window what the page shows:
//     the view and day (for the menus and the window's title), its theme (the title bar follows it), open dialogs and
//     unsaved work. Cmd+W closes the page's open dialog or popover before the window; Cmd+Q asks when the page has work
//     that would be lost.
//   - Closing the window keeps Pulsar running (in the Dock, with today's time not logged as the Dock's badge, and the
//     end-of-day reminder); a click on the Dock icon brings it back. Quitting stops the server this app started
//     (launcher.sh --stop). A server started some other way, such as python3 pulsar.py in a git checkout, keeps running.
//   - Downloads (CSV, the error log) ask where to save; Print opens the print dialog.
//   - A Mac whose Safari is older than 16.2 cannot draw the page: Pulsar then opens in the browser instead.
//
// Built with -D PULSAR_TEST, it also takes a picture, a download and a PDF by itself (see the end of PulsarWindow).

import Cocoa
import ServiceManagement
import UserNotifications
@preconcurrency import WebKit

/// The page's web view: the browser's own items out of its right-click menus, and a pinch that changes the day's row
/// height instead of magnifying the whole window.
final class PulsarWebView: WKWebView {
    var onPinch: ((Int) -> Void)?
    private var pinched: CGFloat = 0

    /// WebKit's items that belong to a browser: Reload would load whatever answers on the port, without the launcher's
    /// check (Pulsar reloads only through View > Reload Pulsar); a new window or a download of a link opens nothing
    /// here; searching the web, sharing or a link to highlighted text of a local address lead nowhere.
    static let browserItems: Set<String> = [
        "WKMenuItemIdentifierReload", "WKMenuItemIdentifierGoBack", "WKMenuItemIdentifierGoForward",
        "WKMenuItemIdentifierOpenLink", "WKMenuItemIdentifierOpenLinkInNewWindow", "WKMenuItemIdentifierDownloadLinkedFile",
        "WKMenuItemIdentifierOpenImageInNewWindow", "WKMenuItemIdentifierDownloadImage", "WKMenuItemIdentifierCopyImage",
        "WKMenuItemIdentifierOpenMediaInNewWindow", "WKMenuItemIdentifierDownloadMedia", "WKMenuItemIdentifierSearchWeb",
        "WKMenuItemIdentifierShareMenu", "WKMenuItemIdentifierCopyLinkWithHighlight", "WKMenuItemIdentifierInspectElement",
        "WKMenuItemIdentifierOpenFrameInNewWindow",
    ]
    /// Writing Tools rewrite text: useful in a note being typed, not over the day's cards.
    static let editingOnly: Set<String> = ["WKMenuItemIdentifierWritingTools", "WKMenuItemIdentifierSummarize"]

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        let editable = menu.items.contains { $0.identifier?.rawValue == "WKMenuItemIdentifierPaste" }
        for item in menu.items {
            let id = item.identifier?.rawValue ?? ""
            if Self.browserItems.contains(id) || (!editable && Self.editingOnly.contains(id)) {
                menu.removeItem(item)
            }
        }
        // no separator first, last or twice in a row
        var lastWasSeparator = true
        for item in menu.items {
            if item.isSeparatorItem && lastWasSeparator { menu.removeItem(item) } else { lastWasSeparator = item.isSeparatorItem }
        }
        while let last = menu.items.last, last.isSeparatorItem { menu.removeItem(last) }
        super.willOpenMenu(menu, with: event)
    }

    override func magnify(with event: NSEvent) {
        if event.phase == .began { pinched = 0 }
        pinched += event.magnification
        if pinched >= 0.3 { pinched = 0; onPinch?(-1) }          // fingers apart: finer rows
        else if pinched <= -0.3 { pinched = 0; onPinch?(1) }     // together: coarser rows
        if event.phase == .ended || event.phase == .cancelled { pinched = 0 }
    }
}

final class PulsarWindow: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation, WKNavigationDelegate,
    WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate, UNUserNotificationCenterDelegate {
    private var window: NSWindow!
    private var web: PulsarWebView!
    private var spinner: NSProgressIndicator!
    private var origin: URL?           // http://127.0.0.1:<port>/ once the launcher has said where Pulsar runs
    private var starting = false
    private var keepServer = false     // true when the page was handed to the browser: it still needs the server
    private var stopped = false
    private var quitAsked = false      // the page asked to quit (Reinstall Pulsar): no question about unsaved work
    private var closeConfirmed = false // the page had nothing open: Cmd+W closes the window itself
    private var message = ""           // the text of the window's own message page, while it shows
    private var page: [String: Any] = [:]  // what the page last said it shows (native.js): view, day, dialogs, …
    private let launcher = Bundle.main.url(forResource: "launcher", withExtension: "sh")
    private let defaults = UserDefaults.standard
    private var running: Process?      // the launcher, while it runs
    private var reminder: Timer?
    private var warp: NativeWarp?
    private static let place = ["view", "date", "zoom", "source", "range", "from", "to"]  // the page's own address keys

    // MARK: start

    func applicationWillFinishLaunching(_ note: Notification) {
        defaults.register(defaults: ["menubar": true, "badge": true, "reminder": true, "reminderTime": "17:30", "pageZoom": 1.0])
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let logDay = UNNotificationAction(identifier: "logday", title: "Log the Day", options: [.foreground])
        center.setNotificationCategories([UNNotificationCategory(identifier: "pulsar.logday", actions: [logDay],
                                                                 intentIdentifiers: [], options: [])])
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildWindow()
        observeSystem()
        let timer = Timer(timeInterval: 60, repeats: true) { [weak self] _ in self?.checkReminder() }
        RunLoop.main.add(timer, forMode: .common)
        reminder = timer
        start()
        if !launchedAtLogin() { NSApp.activate(ignoringOtherApps: true) }  // at login: no taking the keyboard away
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    /// Opened by macOS at login (Open at Login): the window comes up without taking the focus from what is in front.
    private func launchedAtLogin() -> Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return event.eventID == kAEOpenApplication
            && event.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
    }

    /// Runs the launcher and loads the address it prints. The launcher's dialogs are sheets on this window; `quiet`
    /// keeps the page in view meanwhile (a reload, or the warp holding over it while Pulsar restarts into a new
    /// version). The day and view the page shows now are carried into the new address.
    private func start(quiet: Bool = false) {
        guard !starting else { return }
        starting = true
        let carry = isPulsar(web.url) ? (URLComponents(url: web.url!, resolvingAgainstBaseURL: false)?.queryItems ?? [])
            .filter { Self.place.contains($0.name) } : []
        if !quiet { showMessage("Starting Pulsar…", button: nil) }
        runLauncher(["--window"]) { url in
            self.starting = false
            guard let url = url, let base = URL(string: "/", relativeTo: url)?.absoluteURL else {
                NSApp.terminate(nil)   // the launcher has already said why, or the person chose "Not now"
                return
            }
            self.origin = base
            var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            let code = parts?.queryItems ?? []
            parts?.queryItems = code + carry
            self.loadWhenSupported(parts?.url ?? url)
        }
    }

    /// Runs launcher.sh, shows its dialogs as sheets and its progress under the spinner, and hands over the address it
    /// printed, if any. It gives up after five minutes without a word from the launcher (which says something every
    /// few seconds while Apple's developer tools install).
    private func runLauncher(_ args: [String], done: @escaping (URL?) -> Void) {
        guard let launcher = launcher else { done(nil); return }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [launcher.path] + args
        task.environment = ProcessInfo.processInfo.environment.merging(["PULSAR_WINDOW_DIALOGS": "1"]) { $1 }
        let out = Pipe(), input = Pipe()
        task.standardOutput = out
        task.standardError = FileHandle.nullDevice
        task.standardInput = input
        var found: URL?
        var pending = Data()
        var silence: DispatchWorkItem?
        let quietFor: TimeInterval = 300
        let watch = {
            silence?.cancel()
            let item = DispatchWorkItem { task.terminate() }
            silence = item
            DispatchQueue.main.asyncAfter(deadline: .now() + quietFor, execute: item)
        }
        let line = { (text: String) in   // on the main queue
            watch()
            let parts = text.split(separator: " ", maxSplits: 2).map(String.init)
            switch parts.first ?? "" {
            case "PULSAR_URL":
                if parts.count > 1, let url = URL(string: parts[1]), url.scheme == "http", url.host == "127.0.0.1" { found = url }
            case "PULSAR_STATUS":
                if parts.count > 1, let said = Self.unbase64(parts[1]) { self.showMessage(said, button: nil) }
            case "PULSAR_DIALOG":
                let said = parts.count > 1 ? Self.unbase64(parts[1]) ?? "" : ""
                let buttons = (parts.count > 2 ? Self.unbase64(parts[2]) ?? "" : "").split(separator: "\n").map(String.init)
                silence?.cancel()   // the person takes their time
                self.ask(said, buttons: buttons.isEmpty ? ["OK"] : buttons) { answer in
                    watch()
                    try? input.fileHandleForWriting.write(contentsOf: Data((answer + "\n").utf8))
                }
            default: break
            }
        }
        out.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {   // the launcher is done (the server it started holds none of this pipe)
                handle.readabilityHandler = nil
                task.waitUntilExit()
                DispatchQueue.main.async {
                    silence?.cancel()
                    self.running = nil
                    done(found)
                }
                return
            }
            pending.append(data)
            while let nl = pending.firstIndex(of: 10) {
                let text = String(decoding: pending[pending.startIndex..<nl], as: UTF8.self)
                pending.removeSubrange(pending.startIndex...nl)
                DispatchQueue.main.async { line(text) }
            }
        }
        do { try task.run() } catch { out.fileHandleForReading.readabilityHandler = nil; done(nil); return }
        running = task
        watch()
    }

    private static func unbase64(_ s: String) -> String? {
        Data(base64Encoded: s).map { String(decoding: $0, as: UTF8.self) }
    }

    /// One of the launcher's dialogs as a sheet: its first paragraph in bold, the rest under it; the last button is the
    /// default, as in the launcher's own dialogs.
    private func ask(_ text: String, buttons: [String], answer: @escaping (String) -> Void) {
        showWindow()
        spinner.stopAnimation(nil)
        let paragraphs = text.components(separatedBy: "\n\n")
        let alert = NSAlert()
        alert.messageText = paragraphs.first ?? text
        alert.informativeText = paragraphs.dropFirst().joined(separator: "\n\n")
        for title in buttons.reversed() { alert.addButton(withTitle: title) }  // the first one added is the default
        alert.beginSheetModal(for: window) { response in
            let index = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
            let picked = index >= 0 && index < buttons.count ? Array(buttons.reversed())[index] : ""
            if self.starting && !self.isPulsar(self.web.url) { self.spinner.startAnimation(nil) }
            answer(picked)
        }
    }

    /// Runs launcher.sh with the given arguments and returns the address it printed, if any (for --stop, while
    /// quitting, where nothing can be shown).
    @discardableResult
    private func runLauncherAndWait(_ args: [String], timeout: TimeInterval) -> URL? {
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

    // MARK: window

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()   // keeps the session token and the page's choices between launches
        config.userContentController.add(self, name: "pulsar")
        web = PulsarWebView(frame: .zero, configuration: config)
        injectShell()
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsMagnification = false   // a pinch changes the rows instead (PulsarWebView)
        web.onPinch = { [weak self] step in self?.runPage("rowsStep", step) }
        web.pageZoom = CGFloat(defaults.double(forKey: "pageZoom")).clamped(0.5, 2)
        web.underPageBackgroundColor = .windowBackgroundColor

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(width: min(1320, screen.width - 80), height: min(880, screen.height - 60))
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Pulsar"
        // one band with the page's own header: the title bar takes the page's colour and shows no title of its own (the
        // title still names the day for the Window menu, Mission Control and VoiceOver)
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        if #available(macOS 11.0, *) { window.titlebarSeparatorStyle = .none }
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
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.isDisplayedWhenStopped = false
        spinner.sizeToFit()
        spinner.frame.origin = NSPoint(x: (root.bounds.width - spinner.frame.width) / 2, y: root.bounds.height / 2 + 22)
        spinner.autoresizingMask = [.minXMargin, .maxXMargin, .minYMargin, .maxYMargin]
        root.addSubview(spinner)
        warp = NativeWarp(web: web, host: root)
        applyTheme(defaults.string(forKey: "theme") ?? "system", background: defaults.string(forKey: "background"))
        window.center()
        window.setFrameAutosaveName("Pulsar")   // the next launch opens where this one was left
        window.makeKeyAndOrderFront(nil)
    }

    private func showWindow() {
        if !window.isVisible || window.isMiniaturized { window.makeKeyAndOrderFront(nil) }
        NSApp.activate(ignoringOtherApps: true)
    }

    /// What the page learns of the app at the start of every load: its version (for "Reinstall Pulsar"), and the
    /// Mac's own choices the page follows (12 or 24 hours, the first day of the week, the language and region, the
    /// accent colour), and the app's own (the badge, the reminder, Open at Login).
    private func injectShell() {
        let shell: [String: Any] = [
            "version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "",
            "path": Bundle.main.bundlePath, "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "prefs": systemPrefs(), "native": nativePrefs(),
        ]
        let controller = web.configuration.userContentController
        controller.removeAllUserScripts()
        controller.addUserScript(WKUserScript(source: "window.PULSAR_NATIVE_WARP = 1; window.PULSAR_APP = \(Self.json(shell));",
                                              injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }

    private func systemPrefs() -> [String: Any] {
        let hours = DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: Locale.current) ?? "H"
        let region = Locale.current.identifier.split(separator: "@").first.map(String.init) ?? "en_US"
        var accent = ""
        if let c = NSColor.controlAccentColor.usingColorSpace(.sRGB) {
            accent = String(format: "#%02x%02x%02x", Int((c.redComponent * 255).rounded()),
                            Int((c.greenComponent * 255).rounded()), Int((c.blueComponent * 255).rounded()))
        }
        return ["hour12": hours.contains("a"), "firstWeekday": Calendar.current.firstWeekday,
                "locale": region.replacingOccurrences(of: "_", with: "-"), "languages": Locale.preferredLanguages,
                "accent": accent]
    }

    private func nativePrefs() -> [String: Any] {
        ["menubar": defaults.bool(forKey: "menubar"), "badge": defaults.bool(forKey: "badge"), "reminder": defaults.bool(forKey: "reminder"),
         "reminderTime": defaults.string(forKey: "reminderTime") ?? "17:30", "login": loginStatus()]
    }

    /// The page's theme on the window too: the title bar's colour and light or dark, so the two never disagree.
    private func applyTheme(_ theme: String, background: String?) {
        switch theme {
        case "dark": window.appearance = NSAppearance(named: .darkAqua)
        case "light": window.appearance = NSAppearance(named: .aqua)
        default: window.appearance = nil
        }
        if let colour = background.flatMap(Self.cssColour) {
            window.backgroundColor = colour
            web.underPageBackgroundColor = colour
        }
        defaults.set(theme, forKey: "theme")
        if let background = background { defaults.set(background, forKey: "background") }
    }

    /// "rgb(245, 245, 243)" or "rgba(…)" as getComputedStyle gives it.
    static func cssColour(_ css: String) -> NSColor? {
        let numbers = css.split(whereSeparator: { !"0123456789.".contains($0) }).compactMap { Double($0) }
        guard css.hasPrefix("rgb"), numbers.count >= 3 else { return nil }
        return NSColor(srgbRed: numbers[0] / 255, green: numbers[1] / 255, blue: numbers[2] / 255,
                       alpha: numbers.count > 3 ? numbers[3] : 1)
    }

    /// The day after a sleep, a new day at midnight, and the Mac's own choices changing.
    private func observeSystem() {
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            self?.callPage("wake()")
        }
        let nc = NotificationCenter.default
        nc.addObserver(forName: .NSCalendarDayChanged, object: nil, queue: .main) { [weak self] _ in
            self?.callPage("wake()")
        }
        for name in [NSColor.systemColorsDidChangeNotification, NSLocale.currentLocaleDidChangeNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in self?.prefsChanged() }
        }
    }

    private func prefsChanged() {
        injectShell()   // for the next load
        callPage("prefs(\(Self.json(systemPrefs())), \(Self.json(nativePrefs())))")
    }

    /// A message page of the window's own: "Starting Pulsar…" (with the spinner), what the launcher is doing, or what
    /// went wrong with a button to try again.
    private func showMessage(_ text: String, button: String?) {
        if button == nil { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
        if text == message && !isPulsar(web.url) { return }   // the same words: no reload, no flicker
        message = text
        let esc = { (s: String) in s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;") }
        let action = button.map { "<button onclick=\"webkit.messageHandlers.pulsar.postMessage({action:'start'})\">\(esc($0))</button>" } ?? ""
        web.loadHTMLString("""
            <!doctype html><meta charset="utf-8"><style>
            :root { color-scheme: light dark; } html, body { height: 100%; margin: 0; }
            body { display: flex; flex-direction: column; gap: 14px; align-items: center; justify-content: center;
                   font: 13px -apple-system, system-ui, sans-serif; color: CanvasText; background: Canvas;
                   -webkit-user-select: none; cursor: default; }
            p { margin: 0; opacity: .75; max-width: 34em; text-align: center; line-height: 1.45; }
            button { font: inherit; padding: 4px 14px; }
            </style><p>\(esc(text))</p>\(action)
            """, baseURL: nil)
    }

    // MARK: menus

    private func buildMenu() {
        let main = NSMenu()
        func menu(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let holder = NSMenuItem()
            let sub = NSMenu(title: title)
            items.forEach { sub.addItem($0) }
            holder.submenu = sub
            main.addItem(holder)
            return sub
        }
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command,
                  target: AnyObject? = nil) -> NSMenuItem {
            let it = NSMenuItem(title: title, action: action, keyEquivalent: key)
            it.keyEquivalentModifierMask = mods
            it.target = target
            return it
        }
        /// A command the page carries out (native.js, pulsarNative.run).
        func page(_ title: String, _ run: String, _ arg: Any? = nil, _ key: String = "",
                  _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let it = item(title, #selector(pageCommand(_:)), key, mods, target: self)
            it.representedObject = ["run": run, "arg": arg ?? NSNull()]
            return it
        }
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let it = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            it.submenu = NSMenu(title: title)
            items.forEach { it.submenu!.addItem($0) }
            return it
        }
        let left = String(Character(UnicodeScalar(NSLeftArrowFunctionKey)!))
        let right = String(Character(UnicodeScalar(NSRightArrowFunctionKey)!))
        let services = NSMenu(title: "Services")
        let servicesItem = NSMenuItem(title: "Services", action: nil, keyEquivalent: "")
        servicesItem.submenu = services
        _ = menu("Pulsar", [
            item("About Pulsar", #selector(showAbout), target: self),
            page("Check for Updates…", "checkUpdates"),
            .separator(),
            page("Settings…", "settings", nil, ","),
            .separator(),
            servicesItem,
            .separator(),
            item("Hide Pulsar", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit Pulsar", #selector(NSApplication.terminate(_:)), "q"),
        ])
        NSApp.servicesMenu = services
        _ = menu("File", [
            page("New Entry…", "newEntry", nil, "n"),
            page("Log the Day…", "logDay", nil, "l"),
            .separator(),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
            .separator(),
            submenu("Export", [page("Days, CSV", "exportDays"), page("Time Entries, CSV", "exportEntries")]),
            page("Print…", "print", nil, "p"),
        ])
        _ = menu("Edit", [   // without these, Cmd+C and Cmd+V do nothing in the page's fields
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Delete", #selector(NSText.delete(_:))),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        let plus = item("Zoom In", #selector(zoomIn), "+", target: self)   // Cmd+= on a US keyboard is Cmd++ elsewhere
        plus.isHidden = true
        plus.allowsKeyEquivalentWhenHidden = true
        _ = menu("View", [
            page("Day", "day", nil, "1"),
            page("Report", "report", nil, "2"),
            .separator(),
            submenu("Rows", [page("1 Minute", "rows", 1), page("5 Minutes", "rows", 5), page("15 Minutes", "rows", 15),
                             page("30 Minutes", "rows", 30), page("1 Hour", "rows", 60)]),
            .separator(),
            page("Refresh", "refresh", nil, "r"),
            item("Reload Pulsar", #selector(reload), "r", [.command, .option], target: self),
            .separator(),
            item("Actual Size", #selector(zoomReset), "0", target: self),
            item("Zoom In", #selector(zoomIn), "=", target: self),
            plus,
            item("Zoom Out", #selector(zoomOut), "-", target: self),
            .separator(),
            submenu("Appearance", [page("Same as System", "theme", "system"), page("Light", "theme", "light"),
                                   page("Dark", "theme", "dark")]),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        _ = menu("Go", [
            page("Today", "today", nil, "t"),
            page("Previous Day", "prev", nil, left),
            page("Next Day", "next", nil, right),
        ])
        let windowMenu = menu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
            .separator(),
            item("Pulsar", #selector(bringBack), target: self),
            .separator(),
            item("Bring All to Front", #selector(NSApplication.arrangeInFront(_:))),
        ])
        let help = menu("Help", [
            item("Pulsar Help", #selector(openHelp(_:)), target: self),
            item("How Pulsar Works", #selector(openHelp(_:)), target: self),
            .separator(),
            page("Show Error Log", "logs"),
        ])
        help.items[0].representedObject = "https://github.com/fosterinteractive/pulsar#readme"
        help.items[1].representedObject = "https://github.com/fosterinteractive/pulsar/blob/main/docs/HOW-IT-WORKS.md"
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
        NSApp.helpMenu = help
    }

    /// The Dock icon's menu: what can be done without the window in front.
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let menu = NSMenu()
        for (title, run) in [("New Entry…", "newEntry"), ("Log the Day…", "logDay")] {
            let it = NSMenuItem(title: title, action: #selector(pageCommand(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = ["run": run, "arg": NSNull()]
            menu.addItem(it)
        }
        return menu
    }

    @objc private func pageCommand(_ sender: NSMenuItem) {
        guard let spec = sender.representedObject as? [String: Any], let run = spec["run"] as? String else { return }
        if !["theme", "rows"].contains(run) { showWindow() }
        runPage(run, spec["arg"] is NSNull ? nil : spec["arg"])
    }

    /// Greys out what does not apply to what the page shows: the day's commands in the report and the other way
    /// round, everything while a dialog of the page is open, and all of it before the page has loaded.
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(NSWindow.toggleFullScreen(_:)) {
            item.title = window.styleMask.contains(.fullScreen) ? "Exit Full Screen" : "Enter Full Screen"
            return true
        }
        guard item.action == #selector(pageCommand(_:)), let spec = item.representedObject as? [String: Any],
              let run = spec["run"] as? String else { return true }
        let view = page["view"] as? String ?? "day"
        switch run {
        case "day", "report": item.state = view == run ? .on : .off
        case "rows": item.state = (page["zoom"] as? NSNumber)?.intValue == (spec["arg"] as? Int) ? .on : .off
        case "theme": item.state = (page["theme"] as? String ?? "system") == (spec["arg"] as? String) ? .on : .off
        default: break
        }
        let ready = isPulsar(web.url) && page["ready"] as? Bool == true && page["signedIn"] as? Bool != false
        if !ready { return false }
        if page["modal"] as? Bool == true { return false }   // a dialog of the page is open: it comes first
        switch run {
        case "today", "prev", "next", "rows": return view == "day"
        case "print", "exportDays", "exportEntries": return view == "report" && page["report"] as? Bool == true
        default: return true
        }
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

    @objc private func openHelp(_ sender: NSMenuItem) {
        if let s = sender.representedObject as? String, let url = URL(string: s) { NSWorkspace.shared.open(url) }
    }

    @objc private func bringBack() { showWindow() }
    /// Through the launcher, never web.reload(): a plain reload loads whatever answers on the port.
    @objc private func reload() { start(quiet: isPulsar(web.url)) }
    @objc private func zoomReset() { setZoom(1) }
    @objc private func zoomIn() { setZoom(min(2, web.pageZoom + 0.1)) }
    @objc private func zoomOut() { setZoom(max(0.5, web.pageZoom - 0.1)) }
    private func setZoom(_ zoom: CGFloat) {
        web.pageZoom = zoom
        defaults.set(Double(zoom), forKey: "pageZoom")   // the next launch keeps it
    }

    /// pulsarNative.run(name, arg) in the page (web/js/native.js).
    private func runPage(_ run: String, _ arg: Any? = nil) {
        callPage("run(\(Self.json(run)), \(arg.map(Self.json) ?? "null"))")
    }

    private func callPage(_ call: String, done: ((Any?) -> Void)? = nil) {
        guard isPulsar(web.url) else { done?(nil); return }
        web.evaluateJavaScript("window.pulsarNative ? pulsarNative.\(call) : null") { result, _ in done?(result) }
    }

    static func json(_ value: Any) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }

    // MARK: closing and quitting

    /// Cmd+W, or the close button: an open dialog or popover of the page closes first, as a sheet would; only with
    /// nothing open does the window close. Pulsar keeps running then (the Dock icon brings it back).
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if closeConfirmed { closeConfirmed = false; return true }
        guard isPulsar(web.url) else { return true }
        callPage("closeTop()") { closed in
            if (closed as? Bool) != true {
                self.closeConfirmed = true
                self.window.performClose(nil)
            }
        }
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows || !window.isVisible { showWindow() }
        return true
    }

    /// Asks first when the page has work that quitting would lose (a dialog being filled in, Log the day's changes),
    /// then stops the server this app started, unless the page went to the browser. At most a few seconds.
    func applicationShouldTerminate(_ app: NSApplication) -> NSApplication.TerminateReply {
        if !quitAsked, isPulsar(web.url), let lost = page["unsaved"] as? String, !lost.isEmpty {
            showWindow()
            let alert = NSAlert()
            alert.messageText = "Quit Pulsar?"
            alert.informativeText = "\(lost) If you quit now, it is lost."
            alert.addButton(withTitle: "Cancel")
            let quit = alert.addButton(withTitle: "Quit")
            if #available(macOS 11.0, *) { quit.hasDestructiveAction = true }
            alert.beginSheetModal(for: window) { response in
                if response == .alertSecondButtonReturn {
                    self.quitAsked = true
                    self.stopServerThenQuit()
                } else {
                    NSApp.reply(toApplicationShouldTerminate: false)
                }
            }
            return .terminateLater
        }
        guard origin != nil, !keepServer, !stopped else { return .terminateNow }
        stopServerThenQuit()
        return .terminateLater
    }

    private func stopServerThenQuit() {
        guard origin != nil, !keepServer, !stopped else { NSApp.reply(toApplicationShouldTerminate: true); return }
        stopped = true
        running?.terminate()
        window.orderOut(nil)
        DispatchQueue.global().async {
            self.runLauncherAndWait(["--stop"], timeout: 6)
            // Not DispatchQueue.main: while AppKit waits for this answer it runs the modal-panel run loop mode, and
            // when quitting was asked from a block on the main queue, that queue cannot run another block until then.
            self.performSelector(onMainThread: #selector(self.replyTerminate), with: nil, waitUntilDone: false,
                                 modes: [RunLoop.Mode.default.rawValue, RunLoop.Mode.modalPanel.rawValue])
        }
    }

    @objc private func replyTerminate() { NSApp.reply(toApplicationShouldTerminate: true) }

    // MARK: today in the menu bar

    private var statusItem: NSStatusItem?
    private var todayNumbers: (active: Int, logged: Int, notLogged: Int)?

    /// Today's active work and logged time in the menu bar, as ActivityWatch shows its own there: "6:12 · 4:48", with a
    /// menu of the three numbers and the day's commands. It needs no permission, so it works in an app with no
    /// developer signature, where macOS hides the Dock badge (native.js sends the numbers: the "today" message).
    private func showToday(_ body: [String: Any]) {
        let n = { (k: String) in max(0, (body[k] as? NSNumber)?.intValue ?? 0) }
        todayNumbers = (n("active"), n("logged"), n("notLogged"))
        paintStatusItem()
    }

    private func paintStatusItem() {
        guard defaults.bool(forKey: "menubar"), let t = todayNumbers else {
            if let item = statusItem { NSStatusBar.system.removeStatusItem(item); statusItem = nil }
            return
        }
        if statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            if #available(macOS 11.0, *) {
                let image = NSImage(systemSymbolName: "clock", accessibilityDescription: "Pulsar")
                image?.isTemplate = true
                statusItem?.button?.image = image
                statusItem?.button?.imagePosition = .imageLeading
            }
        }
        let hours = { (m: Int) in String(format: "%.1f", Double(m) / 60) }   // decimal hours, as timesheets count them
        let words = { (m: Int) in m >= 60 ? "\(m / 60) h" + (m % 60 > 0 ? " \(m % 60) min" : "") : "\(m) min" }
        statusItem?.button?.font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        statusItem?.button?.title = " \(hours(t.active))h  ✓ \(hours(t.logged))h"   // the clock: active; the tick: logged
        statusItem?.button?.toolTip = "Today: \(hours(t.active)) h active (the clock), \(hours(t.logged)) h logged (the tick)"
        let menu = NSMenu()
        for line in ["Active today: \(hours(t.active)) h (\(words(t.active)))", "Logged: \(hours(t.logged)) h (\(words(t.logged)))",
                     "Not logged: \(hours(t.notLogged)) h (\(words(t.notLogged)))"] {
            let it = NSMenuItem(title: line, action: nil, keyEquivalent: "")
            it.isEnabled = false
            menu.addItem(it)
        }
        menu.addItem(.separator())
        let open = NSMenuItem(title: "Open Pulsar", action: #selector(bringBack), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        for (title, run) in [("Log the Day…", "logDay"), ("New Entry…", "newEntry")] {
            let it = NSMenuItem(title: title, action: #selector(pageCommand(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = ["run": run, "arg": NSNull()] as [String: Any]
            menu.addItem(it)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Pulsar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: ""))
        statusItem?.menu = menu
    }

    // MARK: the Dock badge, the reminder, Open at Login

    /// Today's active time with no entry, on the Dock icon: "45m", "3h". Nothing under a quarter of an hour.
    private func badge(_ minutes: Int) {
        guard defaults.bool(forKey: "badge"), minutes >= 15 else { NSApp.dockTile.badgeLabel = nil; return }
        let label = minutes >= 60 ? "\(Int((Double(minutes) / 60).rounded()))h" : "\(minutes)m"
        NSApp.dockTile.badgeLabel = label
        askForBadges(then: label)
    }

    /// Recent macOS shows a Dock badge only for an app allowed to badge its icon (System Settings > Notifications), so
    /// the first badge asks for that once, together with the reminder's alerts, and puts the label back once allowed.
    private var badgesAsked = false
    private func askForBadges(then label: String) {
        guard !badgesAsked else { return }
        badgesAsked = true
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            center.requestAuthorization(options: [.badge, .alert, .sound]) { granted, _ in
                guard granted else { return }
                DispatchQueue.main.async {
                    if self.defaults.bool(forKey: "badge") { NSApp.dockTile.badgeLabel = label }
                }
            }
        }
    }

    /// Once a working day, at the reminder's time (17:30 unless the page set another), when half an hour or more of
    /// today's active time has no entry: a notification with "Log the Day". If macOS does not let this app notify
    /// (it is not signed by a developer, and some Macs refuse that), the Dock icon bounces once and the window says it.
    private func checkReminder() {
        guard defaults.bool(forKey: "reminder"), isPulsar(web.url) else { return }
        let now = Date(), cal = Calendar.current
        guard !cal.isDateInWeekend(now) else { return }
        let hm = (defaults.string(forKey: "reminderTime") ?? "17:30").split(separator: ":").compactMap { Int($0) }
        guard hm.count == 2, let at = cal.date(bySettingHour: hm[0], minute: hm[1], second: 0, of: now),
              now >= at, now < at.addingTimeInterval(3 * 3600) else { return }
        let day = ISO8601DateFormatter.string(from: now, timeZone: .current, formatOptions: [.withFullDate])
        guard defaults.string(forKey: "reminded") != day else { return }
        defaults.set(day, forKey: "reminded")
        web.callAsyncJavaScript("return window.pulsarNative ? await pulsarNative.todayUnlogged() : null", arguments: [:],
                                in: nil, in: .page) { result in
            guard case .success(let value) = result, let minutes = (value as? NSNumber)?.intValue, minutes >= 30 else { return }
            self.remind(minutes, day: day)
        }
    }

    private func remind(_ minutes: Int, day: String) {
        let said = minutes >= 60 ? "\(minutes / 60) h\(minutes % 60 > 0 ? " \(minutes % 60) min" : "")" : "\(minutes) min"
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.badge, .alert, .sound]) { granted, error in
            DispatchQueue.main.async {
                guard granted, error == nil else { self.remindHere(said); return }
                let content = UNMutableNotificationContent()
                content.title = "Time not logged today"
                content.body = "\(said) of today's work has no entry in Productive."
                content.categoryIdentifier = "pulsar.logday"
                content.sound = .default
                center.add(UNNotificationRequest(identifier: "pulsar.reminder.\(day)", content: content, trigger: nil)) { error in
                    if error != nil { DispatchQueue.main.async { self.remindHere(said) } }
                }
            }
        }
    }

    /// The reminder without notifications: one bounce of the Dock icon, and a sheet on the window.
    private func remindHere(_ said: String) {
        NSApp.requestUserAttention(.informationalRequest)
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Time not logged today"
        alert.informativeText = "\(said) of today's work has no entry in Productive."
        alert.addButton(withTitle: "Log the Day")
        alert.addButton(withTitle: "Later")
        if !window.isVisible { window.orderFront(nil) }
        alert.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn { self.showWindow(); self.runPage("logDayToday") }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        DispatchQueue.main.async {
            self.showWindow()
            if response.actionIdentifier != UNNotificationDismissActionIdentifier { self.runPage("logDayToday") }
        }
        done()
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        if #available(macOS 11.0, *) { done([.banner, .sound]) } else { done([.alert, .sound]) }
    }

    /// Open at Login (macOS 13 and later, SMAppService): "on", "off", "approve" (System Settings must allow it) or
    /// "unsupported" (macOS 12: System Settings > General > Login Items, by hand).
    private func loginStatus() -> String {
        if #available(macOS 13.0, *) {
            switch SMAppService.mainApp.status {
            case .enabled: return "on"
            case .requiresApproval: return "approve"
            default: return "off"
            }
        }
        return "unsupported"
    }

    private func setLogin(_ on: Bool) -> String {
        if #available(macOS 13.0, *) {
            do {
                if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                return error.localizedDescription
            }
        }
        return ""
    }

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

    /// target="_blank" and window.open: never a second window. A link elsewhere goes to the browser only when the
    /// person clicked it in Pulsar's own page, as in decidePolicyFor above: a script's window.open opens nothing.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, ["http", "https", "mailto"].contains(url.scheme ?? ""), !isPulsar(url),
           action.navigationType == .linkActivated, action.sourceFrame.isMainFrame, isPulsarFrame(action.sourceFrame) {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    /// A frame showing Pulsar's own page: its origin is Pulsar's address (scheme, host and port).
    private func isPulsarFrame(_ frame: WKFrameInfo) -> Bool {
        guard let origin = origin else { return false }
        let o = frame.securityOrigin
        return o.protocol == origin.scheme && o.host == origin.host && o.port == (origin.port ?? 0)
    }

    /// The server is not answering (it stopped, or the Mac slept through an update): offer to start it again.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let code = (error as NSError).code
        if code == NSURLErrorCancelled || code == 102 { return }   // 102: a navigation that became a download
        page = [:]
        showMessage("Pulsar is not running.", button: "Start Pulsar")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if isPulsar(web.url) { spinner.stopAnimation(nil); message = "" }
        #if PULSAR_TEST
        testHook()
        #endif
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { page = [:]; start() }

    // MARK: messages from the page

    /// Only Pulsar's own page may ask for anything; the window's own message page ("Pulsar is not running", loaded
    /// with no address) may only ask to start Pulsar again.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        let ownMessagePage = message.frameInfo.request.url.map { $0.absoluteString == "about:blank" } ?? true
        guard isPulsarFrame(message.frameInfo) || (action == "start" && ownMessagePage) else { return }
        switch action {
        case "print": printPage()
        case "warp":   // {to: 0..1, instant, dark}: the page's warp, drawn here on the graphics card
            warp?.to(CGFloat((body["to"] as? NSNumber)?.doubleValue ?? 0), instant: body["instant"] as? Bool ?? false,
                     dark: body["dark"] as? Bool ?? true)
        case "signin", "start": start(quiet: isPulsar(web.url))   // a new one-time code, from the launcher
        case "reload": start(quiet: true)   // the page saw a new version: in again through the launcher (main.js)
        case "state":   // what the page shows (native.js): for the menus, the title and the title bar
            page = body
            if let title = body["title"] as? String, !title.isEmpty { window.title = String(title.prefix(120)) }
            applyTheme(body["theme"] as? String ?? "system", background: body["bg"] as? String)
        case "badge": badge((body["minutes"] as? NSNumber)?.intValue ?? 0)
        case "today": showToday(body)
        case "menu": popUpMenu(body)
        case "quit":   // "Reinstall Pulsar": the new app opens once this one has quit (pulsar/appupdate.py)
            quitAsked = true
            NSApp.terminate(nil)
        case "prefs":  // the page's Settings: {set: {badge, reminder, reminderTime, login}}
            setPrefs(body["set"] as? [String: Any] ?? [:])
        case "openLoginItems":
            if #available(macOS 13.0, *) { SMAppService.openSystemSettingsLoginItems() }
        case "ask":    // {id, what: "prefs"}: answered with pulsarNative.answer(id, value)
            guard let id = (body["id"] as? NSNumber)?.intValue else { return }
            callPage("answer(\(id), \(Self.json(["system": systemPrefs(), "native": nativePrefs()])))")
        default: break
        }
    }

    private func setPrefs(_ set: [String: Any]) {
        var problem = ""
        if let on = set["badge"] as? Bool {
            defaults.set(on, forKey: "badge")
            if !on { NSApp.dockTile.badgeLabel = nil }
        }
        if let on = set["menubar"] as? Bool { defaults.set(on, forKey: "menubar"); paintStatusItem() }
        if let on = set["reminder"] as? Bool { defaults.set(on, forKey: "reminder") }
        if let time = set["reminderTime"] as? String, time.range(of: #"^([01]\d|2[0-3]):[0-5]\d$"#, options: .regularExpression) != nil {
            defaults.set(time, forKey: "reminderTime")
            defaults.removeObject(forKey: "reminded")   // a later time today may still remind
        }
        if let on = set["login"] as? Bool { problem = setLogin(on) }
        injectShell()
        callPage("prefs(\(Self.json(systemPrefs())), \(Self.json(nativePrefs())), \(Self.json(problem)))")
    }

    /// The page's own right-click menu (native.js) as a real menu at the pointer. An item may carry a web address
    /// (Open in Productive): that opens in the browser, https only.
    private func popUpMenu(_ body: [String: Any]) {
        guard let items = body["items"] as? [[String: Any]], !items.isEmpty else { return }
        let menu = NSMenu()
        menu.autoenablesItems = false
        for spec in items.prefix(24) {
            if spec["separator"] as? Bool == true { menu.addItem(.separator()); continue }
            guard let title = spec["title"] as? String, let id = spec["id"] as? String else { continue }
            let item = NSMenuItem(title: String(title.prefix(80)), action: #selector(pickContext(_:)), keyEquivalent: "")
            item.target = self
            item.isEnabled = spec["enabled"] as? Bool ?? true
            item.representedObject = ["id": id, "url": spec["url"] as? String ?? ""]
            menu.addItem(item)
        }
        let zoom = web.pageZoom
        let x = CGFloat((body["x"] as? NSNumber)?.doubleValue ?? 0) * zoom
        let y = CGFloat((body["y"] as? NSNumber)?.doubleValue ?? 0) * zoom
        menu.popUp(positioning: nil, at: NSPoint(x: x, y: web.isFlipped ? y : web.bounds.height - y), in: web)
    }

    @objc private func pickContext(_ item: NSMenuItem) {
        guard let spec = item.representedObject as? [String: String] else { return }
        if let s = spec["url"], !s.isEmpty {
            if let url = URL(string: s), url.scheme == "https" { NSWorkspace.shared.open(url) }
            return
        }
        if let id = spec["id"] { callPage("menuPick(\(Self.json(id)))") }
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

    private func testHook() {
        guard let dir = ProcessInfo.processInfo.environment["PULSAR_TEST_DIR"], isPulsar(web.url), !tested else { return }
        tested = true
        let at = URL(fileURLWithPath: dir)
        if ProcessInfo.processInfo.environment["PULSAR_TEST_WARP"] != nil { testWarp(at); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            let probe = """
                JSON.stringify({signedIn: !!localStorage.getItem('pulsar-session'), address: location.href,
                  signInShown: document.querySelector('#signin').open, title: document.title,
                  supports: CSS.supports('color', 'color-mix(in srgb, red, blue)'),
                  app: !!(window.webkit && webkit.messageHandlers && webkit.messageHandlers.pulsar),
                  shell: window.PULSAR_APP || null, native: !!window.pulsarNative})
                """
            self.web.evaluateJavaScript(probe) { result, _ in
                let state = String(decoding: (try? JSONSerialization.data(withJSONObject: self.page)) ?? Data(), as: UTF8.self)
                try? ((result as? String) ?? "{}").write(to: at.appendingPathComponent("page.json"), atomically: true, encoding: .utf8)
                try? state.write(to: at.appendingPathComponent("state.json"), atomically: true, encoding: .utf8)
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
                            let layers = self.window.contentView?.subviews.filter { !($0 is NSProgressIndicator) }.count ?? -1
                            let json = "{\"native\": \(native as? Bool ?? false), \"viewsAfter\": \(layers)}"
                            try? json.write(to: at.appendingPathComponent("warp.json"), atomically: true, encoding: .utf8)
                            self.quitAsked = true
                            NSApp.terminate(nil)
                        }
                    }
                }
            }
        }
    }

    @objc private func printed(_ op: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.quitAsked = true; NSApp.terminate(nil) }
    }
    #endif
}

extension CGFloat {
    func clamped(_ low: CGFloat, _ high: CGFloat) -> CGFloat { Swift.min(high, Swift.max(low, self)) }
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
        // the page's own background under the picture: the picture shrinks and its hole opens, and the live page must
        // not show there (during an update it is already the new version, loading)
        root.backgroundColor = (web?.underPageBackgroundColor ?? NSColor.windowBackgroundColor).cgColor
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
