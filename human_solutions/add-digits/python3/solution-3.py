def addDigits(num):
    if num == 0:
        return 0
    return (num - 1) % 9 + 1

# Test the function
print(addDigits(38))  # Output: 2
print(addDigits(699))  # Output: 6