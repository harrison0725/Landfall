/* Fast decoder for SAM 3.1 "one_bit" masks.
 *
 * A line-for-line port of _decode_raster and _RangeDecoder in the
 * meta-sam-parser package (pure Python, ~0.5 s per mask). Input is the
 * unpacked byte payload; output is one byte (0/1) per pixel.
 * Returns 0 on success, -1 on a malformed payload.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define PAD 2
#define CONTEXTS (1 << 12)
#define INCREMENT 14
#define COUNT_LIMIT 4096
#define TOP (1u << 24)
#define BOTTOM (1u << 16)

int decode_raster(const uint8_t *in, long n, int width, int height, uint8_t *out) {
    if (n < 4) return -1;
    long pos = 0;
    uint32_t low = 0, range = 0xFFFFFFFFu, code = 0, scaled = 0;
    for (int i = 0; i < 4; i++) code = (code << 8) | in[pos++];
    uint16_t *counts = malloc(sizeof(uint16_t) * CONTEXTS * 2);
    long pw = width + 2 * PAD;
    uint8_t *p = calloc((size_t)pw * (height + PAD), 1);
    if (!counts || !p) { free(counts); free(p); return -1; }
    for (int i = 0; i < CONTEXTS * 2; i++) counts[i] = 1;
    int rc = 0;
    for (int y = 0; y < height && !rc; y++) {
        long row = (long)(y + PAD) * pw + PAD;
        for (int x = 0; x < width; x++) {
            long q = row + x, pr = q - pw, er = q - 2 * pw;
            int ctx = p[q - 1] | (p[q - 2] << 1) | (p[pr - 2] << 2) | (p[pr - 1] << 3) | (p[pr] << 4) |
                      (p[pr + 1] << 5) | (p[pr + 2] << 6) | (p[er - 2] << 7) | (p[er - 1] << 8) |
                      (p[er] << 9) | (p[er + 1] << 10) | (p[er + 2] << 11);
            int off = ctx << 1;
            uint32_t zero = counts[off], one = counts[off + 1], total = zero + one;
            scaled = range / total;
            if (scaled == 0) { rc = -1; break; }
            uint32_t value = (uint32_t)(code - low) / scaled;
            if (value >= total) value = total - 1;
            int bit = value < zero ? 0 : 1;
            uint32_t cum = bit ? zero : 0, freq = bit ? one : zero;
            low = low + scaled * cum;
            range = scaled * freq;
            for (;;) {
                if ((uint32_t)(low ^ (uint32_t)(low + range)) < TOP) {
                } else if (range < BOTTOM) {
                    range = (uint32_t)(-low) & (BOTTOM - 1);
                } else break;
                if (pos >= n) { rc = -1; break; }
                code = (code << 8) | in[pos++];
                low <<= 8;
                range <<= 8;
            }
            if (rc) break;
            p[q] = (uint8_t)bit;
            out[(long)y * width + x] = (uint8_t)bit;
            counts[off + bit] = (uint16_t)(counts[off + bit] + INCREMENT);
            if (counts[off] + counts[off + 1] >= COUNT_LIMIT) {
                counts[off] = (counts[off] >> 1) ? (counts[off] >> 1) : 1;
                counts[off + 1] = (counts[off + 1] >> 1) ? (counts[off + 1] >> 1) : 1;
            }
        }
    }
    free(counts);
    free(p);
    return rc;
}
