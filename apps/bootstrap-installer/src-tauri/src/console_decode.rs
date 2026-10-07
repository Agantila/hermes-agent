//! Decoding of child-process console bytes into text for the installer log.

use encoding_rs::Encoding;

/// Decode one stdout/stderr line from a child process.
///
/// Tokio's `BufReader::lines()` requires valid UTF-8 and aborts the line (with
/// `stream did not contain valid UTF-8`) at the first accented byte (#67193).
/// install.ps1 pins its whole process tree to UTF-8, but text written before
/// that pin (a PowerShell ParserError) or by a child that ignores it still
/// arrives in the system ANSI code page: CP1252 on Western Windows, GBK on
/// zh-CN (#132531). Prefer UTF-8 when the bytes are valid; otherwise decode
/// with the real ANSI code page, so `…` from a cp936 child stays `…` instead
/// of the CP1252 reading `¡­`.
pub(crate) fn decode_console_bytes(bytes: &[u8]) -> String {
    decode_console_bytes_as(bytes, system_ansi_encoding())
}

fn decode_console_bytes_as(bytes: &[u8], fallback: &'static Encoding) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => fallback.decode_without_bom_handling(bytes).0.into_owned(),
    }
}

/// The encoding for a Windows code page number. OEM-only pages encoding_rs
/// does not carry (437, 850, ...) keep the historical Windows-1252 reading.
fn encoding_for_code_page(code_page: u32) -> &'static Encoding {
    u16::try_from(code_page)
        .ok()
        .and_then(codepage::to_encoding)
        .unwrap_or(encoding_rs::WINDOWS_1252)
}

#[cfg(target_os = "windows")]
fn system_ansi_encoding() -> &'static Encoding {
    static ACP: std::sync::OnceLock<&'static Encoding> = std::sync::OnceLock::new();
    // SAFETY: GetACP takes no arguments and only reads process locale state.
    *ACP.get_or_init(|| {
        encoding_for_code_page(unsafe { windows_sys::Win32::Globalization::GetACP() })
    })
}

#[cfg(not(target_os = "windows"))]
fn system_ansi_encoding() -> &'static Encoding {
    encoding_rs::WINDOWS_1252
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_console_bytes_keeps_valid_utf8() {
        assert_eq!(decode_console_bytes("café — ok".as_bytes()), "café — ok");
    }

    #[test]
    fn decode_console_bytes_preserves_cp1252_portuguese_error() {
        // "Não foi fornecido o terminador..." as Windows PowerShell 5.1 emits
        // under CP1252 (0xE3 = ã). BufReader::lines() previously failed here
        // with "stream did not contain valid UTF-8" and the UI only showed "No".
        let bytes: &[u8] = b"N\xE3o foi fornecido o terminador";
        let western = encoding_for_code_page(1252);
        assert_eq!(decode_console_bytes_as(bytes, western), "Não foi fornecido o terminador");
    }

    #[test]
    fn decode_console_bytes_maps_cp1252_only_punctuation() {
        // 0x91/0x92 are curly quotes in Windows-1252, but C1 controls under
        // Latin-1 (`b as char`). This locks the real CP1252 fallback.
        let bytes: &[u8] = b"say \x91hi\x92";
        let western = encoding_for_code_page(1252);
        assert_eq!(decode_console_bytes_as(bytes, western), "say \u{2018}hi\u{2019}");
        assert_ne!(
            decode_console_bytes_as(bytes, western),
            bytes.iter().map(|&b| b as char).collect::<String>(),
            "Latin-1 byte mapping must not be used for the 0x80..=0x9F range"
        );
    }

    #[test]
    fn decode_console_bytes_uses_the_ansi_code_page_not_cp1252() {
        // A cp936 (zh-CN) child's line in real GBK bytes: the CP1252 reading
        // is the `¡­` mojibake in #132531's bootstrap-installer.log.
        let text = "正在准备隔离的 Hermes 运行时… 拒绝访问。";
        let (gbk, _, unmappable) = encoding_rs::GBK.encode(text);
        assert!(!unmappable);
        assert!(std::str::from_utf8(&gbk).is_err());
        assert_eq!(decode_console_bytes_as(&gbk, encoding_for_code_page(936)), text);
        assert!(decode_console_bytes_as(&gbk, encoding_for_code_page(1252)).contains("\u{a1}\u{ad}"));
    }

    #[test]
    fn encoding_for_code_page_covers_cjk_and_keeps_cp1252_for_oem_only_pages() {
        assert_eq!(encoding_for_code_page(936), encoding_rs::GBK);
        assert_eq!(encoding_for_code_page(932), encoding_rs::SHIFT_JIS);
        assert_eq!(encoding_for_code_page(949), encoding_rs::EUC_KR);
        assert_eq!(encoding_for_code_page(950), encoding_rs::BIG5);
        assert_eq!(encoding_for_code_page(1251), encoding_rs::WINDOWS_1251);
        assert_eq!(encoding_for_code_page(437), encoding_rs::WINDOWS_1252);
        assert_eq!(encoding_for_code_page(u32::MAX), encoding_rs::WINDOWS_1252);
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn decode_console_bytes_follows_the_process_acp() {
        // Whatever locale the runner has, the public path decodes with GetACP.
        let acp = unsafe { windows_sys::Win32::Globalization::GetACP() };
        let bytes: &[u8] = b"N\xE3o \xA1\xAD \x91";
        assert_eq!(
            decode_console_bytes(bytes),
            decode_console_bytes_as(bytes, encoding_for_code_page(acp))
        );
    }
}
