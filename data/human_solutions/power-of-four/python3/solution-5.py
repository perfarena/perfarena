def is_power_of_four(n):
    if n == 1:
        return True
    if n <= 0 or n % 4 != 0:
        return False
    return int(log(n, 4)) == log(n, 4)

# Test the function
print(is_power_of_four(16))  # Output: True
print(is_power_of_four(40))  # Output: False