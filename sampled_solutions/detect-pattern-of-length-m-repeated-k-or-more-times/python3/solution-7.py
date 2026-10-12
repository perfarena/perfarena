def max_digit_partition(num):
    max_digit = 0
    for digit in num:
        max_digit = max(max_digit, int(digit))
    return max_digit

# Example usage
num = "12345"
result = max_digit_partition(num)
print(result)  # Output: 5