def fizz_buzz(n):
    res = []
    for x in range(1, n + 1):
        temp = ""
        if x % 3 == 0:
            temp += "fizz"
        if x % 5 == 0:
            temp += "buzz"
        if not temp:
            temp = str(x)
        res.append(temp)
    return res

# Test the function
n = 15
output = fizz_buzz(n)
print(output)