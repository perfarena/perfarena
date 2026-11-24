def count_set_bits(n):
    total = 0
    for i in range(32):
        mask = 1 << i
        if n & mask != 0:
            total += 1
    return total

# Example usage
n = 23
print(count_set_bits(n))  # Output: 4