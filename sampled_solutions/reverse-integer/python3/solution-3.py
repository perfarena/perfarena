def reverse_integer(x):
    if x == 0:
        return 0
    
    # Convert integer to string and remove leading zeros
    x_str = str(abs(x)).strip('0')
    
    # Reverse the string
    reversed_str = x_str[::-1]
    
    # Check for overflow
    if int(reversed_str) > 2**31 - 1 or int(reversed_str) < -2**31:
        return 0
    
    # Return the reversed integer with appropriate sign
    if x < 0:
        return -int(reversed_str)
    else:
        return int(reversed_str)

# Test the function
print(reverse_integer(123))  # Output: 321
print(reverse_integer(-123))  # Output: -321
print(reverse_integer(120))  # Output: 21