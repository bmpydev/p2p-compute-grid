use wasm_bindgen::prelude::*;
use sha2::{Sha256, Digest};

#[wasm_bindgen]
pub struct RenderResult {
    pixels: Vec<u8>,
    hash: String,
}

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
impl RenderResult {
    #[wasm_bindgen(getter)]
    pub fn pixels(&self) -> Vec<u8> { self.pixels.clone() }
    #[wasm_bindgen(getter)]
    pub fn hash(&self) -> String { self.hash.clone() }
}

#[wasm_bindgen]
pub fn render_mandelbrot_secure(width: u32, height: u32, start_x: f64, start_y: f64, zoom: f64) -> RenderResult {
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    let max_iter = 255;

    // ... (Hier bleibt dein Mandelbrot-Loop exakt gleich) ...
    for y in 0..height {
        for x in 0..width {
            let cx = start_x + (x as f64 / width as f64) * zoom;
            let cy = start_y + (y as f64 / height as f64) * zoom;
            let (mut zx, mut zy, mut i) = (0.0, 0.0, 0);
            while zx * zx + zy * zy <= 4.0 && i < max_iter {
                let xtemp = zx * zx - zy * zy + cx;
                zy = 2.0 * zx * zy + cy;
                zx = xtemp;
                i += 1;
            }
            pixels.push(i as u8); pixels.push((i / 2) as u8);
            pixels.push((i * 2) as u8); pixels.push(255);
        }
    }

    // --- NEU: SHA-256 HASH BERECHNEN ---
    let mut hasher = Sha256::new();
    hasher.update(&pixels);
    let hash_result = format!("{:x}", hasher.finalize());

    RenderResult { pixels, hash: hash_result }
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

#[wasm_bindgen]
pub fn render_mandelbrot(width: u32, height: u32, start_x: f64, start_y: f64, zoom: f64) -> Vec<u8> {
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    let max_iter = 255;

    for y in 0..height {
        for x in 0..width {
            // Berechne die komplexen Koordinaten
            let cx = start_x + (x as f64 / width as f64) * zoom;
            let cy = start_y + (y as f64 / height as f64) * zoom;

            let mut zx = 0.0;
            let mut zy = 0.0;
            let mut i = 0;

            while zx * zx + zy * zy <= 4.0 && i < max_iter {
                let xtemp = zx * zx - zy * zy + cx;
                zy = 2.0 * zx * zy + cy;
                zx = xtemp;
                i += 1;
            }

            // Farbe basierend auf Iterationen (einfaches Blau/Schwarz)
            pixels.push(i as u8);       // R
            pixels.push((i / 2) as u8); // G
            pixels.push((i * 2) as u8); // B
            pixels.push(255);           // Alpha
        }
    }
    pixels
}
