def addDigits(num):
    if num == 0:
        return 0
    if num % 9 == 0:
        return 9
    return num % 9

# Test the function with different numbers
print(addDigits(38))  # Output: 2
print(addDigits(17))  # Output: 8
print(addDigits(32))  # Output: 5