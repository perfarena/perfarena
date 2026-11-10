def minDeciBinaryNumbers(n):
    result = 0
    for digit in str(n):
        digit_int = int(digit)
        if digit_int > result:
            result = digit_int
    return result

# Test the function
n = 9326
print(minDeciBinaryNumbers(n))  # Output: 9