def count_set_bits(n):
    result = 0
    while n != 0:
        if n % 2 == 1:
            result += 1
        n >>= 1  # Right shift by 1 position
    return result