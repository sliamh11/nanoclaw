import sharp from 'sharp';

const MAX_DIMENSION = 1024;
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB

/** Resize raw image bytes to JPEG base64. Returns null on invalid/oversized input. */
export async function resizeAndEncode(raw: Buffer): Promise<string | null> {
  if (raw.length === 0 || raw.length > MAX_FILE_SIZE) return null;

  try {
    const resized = await sharp(raw)
      .resize(MAX_DIMENSION, MAX_DIMENSION, {
        fit: 'inside',
        withoutEnlargement: true,
      })
      .jpeg({ quality: 85 })
      .toBuffer();

    return resized.toString('base64');
  } catch {
    return null;
  }
}
