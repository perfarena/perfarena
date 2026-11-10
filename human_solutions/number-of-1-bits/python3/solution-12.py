def count_one_bits_iterative(n):
    count = 0
    mask = 1
    for _ in range(32):
        if n & mask != 0:
            count += 1
        mask <<= 1
    return count