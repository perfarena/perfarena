def min_dc_binary_numbers(n):
    result = 0
    for digit in n:
        result = max(result, int(digit) - ord('0'))
    return result

# Example usage
n = "32"
print(min_dc_binary_numbers(n))  # Output: 9