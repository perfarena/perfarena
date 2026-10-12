def addDigits(num):
    if num == 0:
        return 0
    if num % 9 == 0:
        return 9
    return num % 9

# Test the function
print(addDigits(38))  # Output should be 2