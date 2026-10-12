def count_one_bits(n):
    count = 0
    while n != 0:
        n = n & (n - 1)
        count += 1
    return count

# Example usage
n = 13
result = count_one_bits(n)
print(result)  # Output: 3