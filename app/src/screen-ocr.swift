// Prints the text on a screenshot (zh-Hant + en) using Apple Vision on-device OCR.
// Tables are rendered as Markdown rows. Usage: screen-ocr <image>
import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count > 1,
      let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    FileHandle.standardError.write("usage: screen-ocr <image>\n".data(using: .utf8)!)
    exit(2)
}

@available(macOS 26, *)
func recognize(_ image: CGImage) async throws {
    var request = RecognizeDocumentsRequest()
    request.textRecognitionOptions.recognitionLanguages = [
        Locale.Language(identifier: "zh-Hant"), Locale.Language(identifier: "zh-Hans"), Locale.Language(identifier: "en-US"),
    ]
    let observations = try await request.perform(on: image)
    for observation in observations {
        let doc = observation.document
        print(doc.text.transcript)
        for table in doc.tables {
            print("")
            for row in table.rows {
                let cells = row.map { $0.content.text.transcript.replacingOccurrences(of: "\n", with: " ") }
                print("| " + cells.joined(separator: " | ") + " |")
            }
        }
    }
}

let done = DispatchSemaphore(value: 0)
Task {
    guard #available(macOS 26, *) else {
        // Built for macOS 15 so the app's helpers all run there; document OCR itself needs 26.
        FileHandle.standardError.write("screen-ocr needs macOS 26 (RecognizeDocumentsRequest)\n".data(using: .utf8)!)
        exit(3)
    }
    do {
        try await recognize(image)
    } catch {
        FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
    done.signal()
}
done.wait()
