// pulsar-sign: a release key kept in this Mac's Secure Enclave, used only with a fingerprint.
//
//   pulsar-sign keygen KEYFILE [--no-touch-id]   make a key in the Secure Enclave, print its public key (PEM)
//   pulsar-sign pubkey KEYFILE                   print the public key again
//   pulsar-sign sign KEYFILE IN OUT [WHAT]       sign IN (ECDSA P-256 over its SHA-256, DER, as `openssl dgst
//                                                -sha256 -sign` makes it) into OUT, after Touch ID
//
// The private key never leaves the chip. KEYFILE holds only a handle that this Mac's Secure Enclave alone can use:
// copied to another Mac, or read by any program, it signs nothing. Every signature asks for a fingerprint, and the
// Touch ID dialog names what is signed and the start of its SHA-256, so a program cannot sign quietly. Any enrolled
// finger works (enrolling one needs the Mac's password), so adding a fingerprint later does not lose the key.
// --no-touch-id makes a key that signs without asking: for tests only, never a release key.
//
// Built by macos/sign.sh with the Mac's own swiftc; standard frameworks only.

import CryptoKit
import Foundation
import LocalAuthentication
import Security

func fail(_ text: String) -> Never {
    FileHandle.standardError.write(("pulsar-sign: " + text + "\n").data(using: .utf8)!)
    exit(1)
}

func read(_ path: String) -> Data {
    guard let data = FileManager.default.contents(atPath: path) else { fail("cannot read \(path)") }
    return data
}

func hex(_ digest: SHA256.Digest) -> String { digest.map { String(format: "%02x", $0) }.joined() }

func loadKey(_ path: String, context: LAContext? = nil) -> SecureEnclave.P256.Signing.PrivateKey {
    do {
        return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: read(path), authenticationContext: context)
    } catch {
        fail("\(path) is not a Secure Enclave key of this Mac (\(error.localizedDescription))")
    }
}

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else { fail("usage: keygen KEYFILE [--no-touch-id] | pubkey KEYFILE | sign KEYFILE IN OUT [WHAT]") }

switch command {
case "keygen":
    guard args.count >= 2 else { fail("usage: keygen KEYFILE [--no-touch-id]") }
    guard SecureEnclave.isAvailable else { fail("this Mac has no Secure Enclave") }
    let path = args[1]
    var flags: SecAccessControlCreateFlags = [.privateKeyUsage]
    if !args.contains("--no-touch-id") { flags.insert(.biometryAny) }
    var error: Unmanaged<CFError>?
    guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, flags, &error)
    else { fail("access control: \(String(describing: error?.takeRetainedValue()))") }
    let key: SecureEnclave.P256.Signing.PrivateKey
    do { key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access) } catch {
        fail("the Secure Enclave made no key (\(error.localizedDescription))")
    }
    // created new and readable by this user only: an existing key file is never overwritten
    let fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
    guard fd >= 0 else { fail("\(path) exists already or cannot be created: a key is never overwritten") }
    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    handle.write(key.dataRepresentation)
    print(key.publicKey.pemRepresentation)

case "pubkey":
    guard args.count == 2 else { fail("usage: pubkey KEYFILE") }
    print(loadKey(args[1]).publicKey.pemRepresentation)

case "sign":
    guard args.count == 4 || args.count == 5 else { fail("usage: sign KEYFILE IN OUT [WHAT]") }
    let data = read(args[2])
    let digest = hex(SHA256.hash(data: data))
    let what = args.count == 5 ? args[4] : (args[2] as NSString).lastPathComponent
    let context = LAContext()
    context.localizedReason = "sign \(what) (SHA-256 \(digest.prefix(12))…)"
    FileHandle.standardError.write("Touch ID: sign \(what), SHA-256 \(digest)\n".data(using: .utf8)!)
    let key = loadKey(args[1], context: context)
    let signature: P256.Signing.ECDSASignature
    do { signature = try key.signature(for: data) } catch {
        fail("not signed (\(error.localizedDescription))")
    }
    guard key.publicKey.isValidSignature(signature, for: data) else { fail("the signature does not check out") }
    guard FileManager.default.createFile(atPath: args[3], contents: signature.derRepresentation) else {
        fail("cannot write \(args[3])")
    }

default:
    fail("unknown command \(command)")
}
