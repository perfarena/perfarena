def reverse(x):
    result = 0
    negative = x < 0
    if negative:
        x = -x

    while x > 0:
        digit = x % 10
        result = result * 10 + digit
        x //= 10

    if negative:
        result = -result

    if -(2**31) <= result <= 2**31 - 1:
        return result
    else:
        return 0

# Test the implementation with sample cases
print(reverse(123))  # Output should be 321
print(reverse(-123))  # Output should be -321