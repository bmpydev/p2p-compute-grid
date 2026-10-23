use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn process_image_chunk(mut pixels: Vec<u8>) -> Vec<u8> {
    // pixels ist ein Array im Format [R, G, B, A, R, G, B, A, ...]
    // chunks_exact_mut: ein unvollständiger Rest (len % 4 != 0) bleibt unverändert statt zu paniken
    for px in pixels.chunks_exact_mut(4) {
        // Beispiel: Farben invertieren
        px[0] = 255 - px[0]; // Red
        px[1] = 255 - px[1]; // Green
        px[2] = 255 - px[2]; // Blue
        // px[3] ist Alpha (lassen wir so)
    }
    pixels
}

#[wasm_bindgen]
pub fn run_benchmark() -> f64 {
    let mut x = 0.5f64;
    let iterations = 10_000_000;
    // Wir führen in der Schleife ca. 5 Operationen pro Durchgang aus (+, *, sqrt, sin, /)
    for i in 0..iterations {
        x = (x + i as f64).sqrt().sin() * 0.999;
    }
    x // Rückgabe nur zur Vermeidung von Optimierung
}

#[wasm_bindgen]
pub fn process_sobel(pixels: &[u8], width: u32, height: u32) -> Vec<u8> {
    // Abmessungen müssen exakt zu den Daten passen, sonst leeres Ergebnis statt Panic
    let expected = (width as usize).checked_mul(height as usize).and_then(|n| n.checked_mul(4));
    if expected != Some(pixels.len()) {
        return Vec::new();
    }
    let mut output = vec![0u8; pixels.len()];

    for y in 1..height.saturating_sub(1) {
        for x in 1..width.saturating_sub(1) {
            let idx = (y * width + x) as usize * 4;

            // Graustufen-Berechnung für Sobel (vereinfacht)
            let get_gray = |dx: i32, dy: i32| {
                let i = ((y as i32 + dy) * width as i32 + (x as i32 + dx)) as usize * 4;
                (pixels[i] as i32 + pixels[i+1] as i32 + pixels[i+2] as i32) / 3
            };

            // Sobel Kernel (Horizontal & Vertikal)
            let gx = -get_gray(-1, -1) + get_gray(1, -1)
            - 2 * get_gray(-1, 0) + 2 * get_gray(1, 0)
            - get_gray(-1, 1) + get_gray(1, 1);

            let gy = -get_gray(-1, -1) - 2 * get_gray(0, -1) - get_gray(1, -1)
            + get_gray(-1, 1) + 2 * get_gray(0, 1) + get_gray(1, 1);

            let mag = ((gx*gx + gy*gy) as f32).sqrt() as u8;

            output[idx] = mag;     // R
            output[idx+1] = mag;   // G
            output[idx+2] = mag;   // B
            output[idx+3] = 255;   // A
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sobel_rejects_mismatched_dimensions() {
        assert!(process_sobel(&[0; 16], 0, 0).is_empty());
        assert!(process_sobel(&[0; 16], 2, 3).is_empty());
        assert!(process_sobel(&[0; 16], u32::MAX, u32::MAX).is_empty());
    }

    #[test]
    fn sobel_handles_tiny_images() {
        assert_eq!(process_sobel(&[7; 4], 1, 1), vec![0; 4]);
        assert_eq!(process_sobel(&[7; 8], 2, 1), vec![0; 8]);
        assert_eq!(process_sobel(&[7; 20], 1, 5), vec![0; 20]);
    }

    #[test]
    fn sobel_detects_vertical_edge() {
        // 3x3: linke Spalte schwarz, Rest weiß -> Kante im Mittelpixel
        let mut px = vec![255u8; 3 * 3 * 4];
        for y in 0..3 { for c in 0..3 { px[(y * 3) * 4 + c] = 0; } }
        let out = process_sobel(&px, 3, 3);
        assert_eq!(&out[16..20], &[255, 255, 255, 255]);
        assert_eq!(&out[0..4], &[0, 0, 0, 0]); // Rand bleibt unberechnet
    }

    #[test]
    fn invert_ignores_incomplete_trailing_pixel() {
        assert_eq!(process_image_chunk(vec![0, 10, 200, 7, 1, 2]), vec![255, 245, 55, 7, 1, 2]);
    }
}
