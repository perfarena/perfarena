def count_one_bits_optimized(n):
    result = 0
    while n != 0:
        n = n & (n - 1)
        result += 1
    return result