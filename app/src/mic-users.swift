// Lists processes currently capturing audio input (microphone), one per line:
// <pid>\t<bundle id or executable path>
import CoreAudio
import Foundation

func getProp<T>(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> Bool {
    var addr = AudioObjectPropertyAddress(mSelector: selector,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &value) == noErr
}

var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                      mScope: kAudioObjectPropertyScopeGlobal,
                                      mElement: kAudioObjectPropertyElementMain)
var size: UInt32 = 0
guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else { exit(1) }
var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { exit(1) }

for id in ids {
    var running: UInt32 = 0
    guard getProp(id, kAudioProcessPropertyIsRunningInput, &running), running != 0 else { continue }
    var pid: pid_t = 0
    _ = getProp(id, kAudioProcessPropertyPID, &pid)
    var bundle: CFString = "" as CFString
    _ = getProp(id, kAudioProcessPropertyBundleID, &bundle)
    var name = bundle as String
    if name.isEmpty {
        var buf = [CChar](repeating: 0, count: 4096)
        if proc_pidpath(pid, &buf, UInt32(buf.count)) > 0 { name = String(cString: buf) }
    }
    print("\(pid)\t\(name)")
}
