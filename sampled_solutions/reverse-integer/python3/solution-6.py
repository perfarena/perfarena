# Initialize the result variable
result = 0

# Check if the input integer is negative
if x < 0:
    is_negative = True
    x = -x
else:
    is_negative = False

# Convert the integer to a string and reverse it
if is_negative:
    str_x = str(x)[1:][::-1]
    result = int('-' + str_x)
else:
    str_x = str(x)[::-1]
    result = int(str_x)

# Check if the result exceeds the limits
if result > 2**31 - 1 or result < -2**31:
    return 0

return result