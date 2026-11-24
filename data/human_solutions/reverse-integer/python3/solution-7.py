# Initialize the result variable
result = 0

# Check if the input integer is negative
if x < 0:
    is_negative = True
    x = -x
else:
    is_negative = False

# Reverse the integer using remainders
while x > 0:
    result = result * 10 + x % 10
    x //= 10

# Check if the result exceeds the limits
if result > 2**31 - 1 or result < -2**31:
    return 0

if is_negative:
    result *= -1

return result