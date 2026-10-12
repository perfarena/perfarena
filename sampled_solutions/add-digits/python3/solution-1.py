def addDigits(num):
    if num == 0:
        return 0
    if num % 9 == 0:
        return 9
    return num % 9

# Test the function
num = 38
print(addDigits(num))  # Output should be 2