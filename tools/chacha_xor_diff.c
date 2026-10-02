// SPDX-License-Identifier: GPL-2.0-or-later
/* chacha_permute and chacha_block_generic mirror Linux 6.18
 * lib/crypto/chacha-block-generic.c (Copyright (C) 2015 Martin Willi). */
/* Differential test of the fused chacha_xor_generic (patch 0006)
 * against the kernel's original chacha_block_generic + xor path, run on the
 * big-endian board. Kernel helpers are mirrored with their BE semantics. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32; typedef uint8_t u8;
static inline u32 rol32(u32 v, int s) { return (v << s) | (v >> (32 - s)); }
static inline u32 swab32(u32 x) { return (x << 24) | ((x & 0xff00) << 8) | ((x >> 8) & 0xff00) | (x >> 24); }
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#define cpu_to_le32(x) swab32(x)
#else
#define cpu_to_le32(x) (x)
#endif
static inline u32 get_unaligned32(const void *p) { u32 v; memcpy(&v, p, 4); return v; }
static inline void put_unaligned32(u32 v, void *p) { memcpy(p, &v, 4); }
static inline void put_unaligned_le32(u32 v, u8 *p) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
struct chacha_state { u32 x[16]; };
static void chacha_permute(struct chacha_state *state, int nrounds) {
	u32 *x = state->x;
	for (int i = 0; i < nrounds; i += 2) {
		x[0] += x[4]; x[12] = rol32(x[12] ^ x[0], 16); x[1] += x[5]; x[13] = rol32(x[13] ^ x[1], 16);
		x[2] += x[6]; x[14] = rol32(x[14] ^ x[2], 16); x[3] += x[7]; x[15] = rol32(x[15] ^ x[3], 16);
		x[8] += x[12]; x[4] = rol32(x[4] ^ x[8], 12); x[9] += x[13]; x[5] = rol32(x[5] ^ x[9], 12);
		x[10] += x[14]; x[6] = rol32(x[6] ^ x[10], 12); x[11] += x[15]; x[7] = rol32(x[7] ^ x[11], 12);
		x[0] += x[4]; x[12] = rol32(x[12] ^ x[0], 8); x[1] += x[5]; x[13] = rol32(x[13] ^ x[1], 8);
		x[2] += x[6]; x[14] = rol32(x[14] ^ x[2], 8); x[3] += x[7]; x[15] = rol32(x[15] ^ x[3], 8);
		x[8] += x[12]; x[4] = rol32(x[4] ^ x[8], 7); x[9] += x[13]; x[5] = rol32(x[5] ^ x[9], 7);
		x[10] += x[14]; x[6] = rol32(x[6] ^ x[10], 7); x[11] += x[15]; x[7] = rol32(x[7] ^ x[11], 7);
		x[0] += x[5]; x[15] = rol32(x[15] ^ x[0], 16); x[1] += x[6]; x[12] = rol32(x[12] ^ x[1], 16);
		x[2] += x[7]; x[13] = rol32(x[13] ^ x[2], 16); x[3] += x[4]; x[14] = rol32(x[14] ^ x[3], 16);
		x[10] += x[15]; x[5] = rol32(x[5] ^ x[10], 12); x[11] += x[12]; x[6] = rol32(x[6] ^ x[11], 12);
		x[8] += x[13]; x[7] = rol32(x[7] ^ x[8], 12); x[9] += x[14]; x[4] = rol32(x[4] ^ x[9], 12);
		x[0] += x[5]; x[15] = rol32(x[15] ^ x[0], 8); x[1] += x[6]; x[12] = rol32(x[12] ^ x[1], 8);
		x[2] += x[7]; x[13] = rol32(x[13] ^ x[2], 8); x[3] += x[4]; x[14] = rol32(x[14] ^ x[3], 8);
		x[10] += x[15]; x[5] = rol32(x[5] ^ x[10], 7); x[11] += x[12]; x[6] = rol32(x[6] ^ x[11], 7);
		x[8] += x[13]; x[7] = rol32(x[7] ^ x[8], 7); x[9] += x[14]; x[4] = rol32(x[4] ^ x[9], 7);
	}
}
/* Original kernel path. */
static void chacha_block_generic(struct chacha_state *state, u8 *out, int nrounds) {
	struct chacha_state p = *state;
	chacha_permute(&p, nrounds);
	for (int i = 0; i < 16; i++) put_unaligned_le32(p.x[i] + state->x[i], &out[i * 4]);
	state->x[12]++;
}
static void orig_crypt(struct chacha_state *state, u8 *dst, const u8 *src, unsigned bytes, int nrounds) {
	u8 stream[64];
	while (bytes >= 64) { chacha_block_generic(state, stream, nrounds);
		for (int i = 0; i < 64; i++) dst[i] = src[i] ^ stream[i]; bytes -= 64; dst += 64; src += 64; }
	if (bytes) { chacha_block_generic(state, stream, nrounds); for (unsigned i = 0; i < bytes; i++) dst[i] = src[i] ^ stream[i]; }
}
/* Patch 0006 body, verbatim apart from the helper names. */
static void chacha_xor_generic(struct chacha_state *state, u8 *dst, const u8 *src, unsigned int bytes, int nrounds) {
	struct chacha_state x; int i;
	while (bytes >= 64) {
		x = *state; chacha_permute(&x, nrounds);
		for (i = 0; i < 16; i++) { u32 k = cpu_to_le32(x.x[i] + state->x[i]);
			put_unaligned32(get_unaligned32((const u32 *)src + i) ^ k, (u32 *)dst + i); }
		state->x[12]++; bytes -= 64; dst += 64; src += 64;
	}
	if (bytes) { u8 stream[64]; chacha_block_generic(state, stream, nrounds);
		for (i = 0; i < (int)bytes; i++) dst[i] = src[i] ^ stream[i]; }
}
static u8 a[2100], b[2100], c[2100], d[2100];
int main(void) {
	srand(12345); unsigned fails = 0, cases = 0;
	for (int t = 0; t < 5000; t++) {
		struct chacha_state s1, s2; for (int i = 0; i < 16; i++) s1.x[i] = (u32)rand() * 2654435761u + i;
		s2 = s1; unsigned len = rand() % 2001; int so = rand() % 4, dof = rand() % 4, inplace = rand() % 2;
		int rounds = (rand() % 4 == 0) ? 12 : 20;
		for (unsigned i = 0; i < len + 4; i++) a[i] = rand();
		memcpy(c, a, sizeof(a));
		if (inplace) { orig_crypt(&s1, a + so, a + so, len, rounds); chacha_xor_generic(&s2, c + so, c + so, len, rounds);
			if (memcmp(a, c, len + 4)) fails++; }
		else { orig_crypt(&s1, b + dof, a + so, len, rounds); chacha_xor_generic(&s2, d + dof, c + so, len, rounds);
			if (memcmp(b + dof, d + dof, len)) fails++; }
		if (memcmp(&s1, &s2, sizeof(s1))) fails++;
		cases++;
	}
	printf("{\"cases\":%u,\"failures\":%u,\"big_endian\":%d}\n", cases, fails, __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__);
	return fails != 0;
}
