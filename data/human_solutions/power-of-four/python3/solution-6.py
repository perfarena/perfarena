def is_power_of_four(n):
    return n > 0 and (n & (n - 1) == 0) and (n & 0xaaaaaaaa == 0)

# Test the function
print(is_power_of_four(16))  # Output: True
print(is_power_of_four(5))   # Output: False