def find_largest_odd_number(num):
    n = len(num)
    for i in range(n - 1, -1, -1):
        digit = int(num[i])
        if digit % 2 != 0:
            return num[:i+1]
    return ""

# Test the function with sample input
num = "1234"
result = find_largest_odd_number(num)
print(result)