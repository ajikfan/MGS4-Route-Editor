/* Decodeur de blocs LZ4 bruts pour l'editeur (mgs4routes/vpak.py, par ctypes).
 * Le decodeur Python mettait ~10 s sur les textures HD d'une zone a sa premiere
 * ouverture. Compilation (gcc de msys2, mingw64) :
 *   gcc -O2 -shared -s -static -o mgs4routes/lz4dec.dll native/lz4dec.c
 * Retour : octets ecrits, ou -1 si le bloc est invalide (jamais d'ecriture hors
 * de dst ni de lecture hors de src). */
#include <stddef.h>
#include <stdint.h>

__declspec(dllexport) long long lz4_block_decode(const uint8_t *src, long long src_size,
                                                 uint8_t *dst, long long dst_size)
{
    const uint8_t *ip = src, *iend = src + src_size;
    uint8_t *op = dst, *oend = dst + dst_size;
    while (ip < iend) {
        unsigned token = *ip++;
        size_t literals = token >> 4;
        if (literals == 15) {
            unsigned extra;
            do {
                if (ip >= iend) return -1;
                extra = *ip++;
                literals += extra;
            } while (extra == 255);
        }
        if ((size_t)(iend - ip) < literals || (size_t)(oend - op) < literals) return -1;
        for (size_t k = 0; k < literals; k++) op[k] = ip[k];
        ip += literals;
        op += literals;
        if (ip >= iend || op >= oend) break;
        if (iend - ip < 2) return -1;
        size_t distance = ip[0] | (ip[1] << 8);
        ip += 2;
        size_t length = (token & 15) + 4;
        if (length == 19) {
            unsigned extra;
            do {
                if (ip >= iend) return -1;
                extra = *ip++;
                length += extra;
            } while (extra == 255);
        }
        if (!distance || distance > (size_t)(op - dst) || (size_t)(oend - op) < length) return -1;
        const uint8_t *match = op - distance;
        for (size_t k = 0; k < length; k++) op[k] = match[k];  /* chevauchement voulu */
        op += length;
    }
    return (long long)(op - dst);
}
