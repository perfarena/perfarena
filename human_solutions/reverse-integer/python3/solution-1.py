def reverse_integer(x):
    reverse = 0
    is_negative = x < 0
    x = abs(x)
    
    while x != 0:
        digit = x % 10
        if reverse > (2**31 - 1) // 10 or (reverse == (2**31 - 1) // 10 and digit > 7):
            return 0
        reverse = reverse * 10 + digit
        x //= 10
    
    return -reverse if is_negative else reverse

# Test the function with sample inputs
print(reverse_integer(123))  # Output: 321
print(reverse_integer(-123))  # Output: -321