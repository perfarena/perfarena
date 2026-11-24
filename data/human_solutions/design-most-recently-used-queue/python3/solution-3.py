def largest_odd_number(num: str) -> str:
    for i in range(len(num) - 1, -1, -1):
        if int(num[i]) % 2 != 0:
            return num[:i+1]
    return ""

# Test the function
num = "52"
result = largest_odd_number(num)
print(result)  # Output: "5"