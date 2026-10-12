def count_set_bits(n):
    count = 0
    while n:
        n &= (n - 1)
        count += 1
    return count

# Example usage
num = 9  # Binary representation: 1001
print(count_set_bits(num))  # Output: 2 (Number of set bits in the binary representation)