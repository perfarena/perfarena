def power_of_4(n):
    if n <= 0:
        return False
    
    for i in range(16):  # Check up to 4^15
        if 4 ** i == n:
            return True
    
    return False

# Test the function with sample test cases
print(power_of_4(16))  # Output: True
print(power_of_4(5))   # Output: False