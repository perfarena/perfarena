# Using built-in function
if n <= 0:
    return False
return int(math.log(n, 4)) == math.log(n, 4)

# Without using built-in function
if n <= 0:
    return False
if n == 1:
    return True
while n > 1:
    if n % 4 != 0:
        return False
    n /= 4
return n == 1