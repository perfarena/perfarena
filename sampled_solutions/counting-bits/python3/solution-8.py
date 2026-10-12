def count_set_bits(n):
    result = 0
    if n == 0:
        return 0
    while n > 0:
        result += n & 1
        n >>= 1
    return result

# Test the function
n = 5
print(count_set_bits(n))  # Output: 2