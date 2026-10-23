use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn process_image_chunk(mut pixels: Vec<u8>) -> Vec<u8> {
    // pixels ist ein Array im Format [R, G, B, A, R, G, B, A, ...]
    for i in (0..pixels.len()).step_by(4) {
        // Beispiel: Farben invertieren
        pixels[i]     = 255 - pixels[i];     // Red
        pixels[i + 1] = 255 - pixels[i + 1]; // Green
        pixels[i + 2] = 255 - pixels[i + 2]; // Blue
        // pixels[i + 3] ist Alpha (lassen wir so)
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
    let mut output = vec![0u8; pixels.len()];

    for y in 1..height-1 {
        for x in 1..width-1 {
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
