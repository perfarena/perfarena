def largest_odd_number(s):
    n = len(s)
    for i in range(n - 1, -1, -1):
        if int(s[i]) % 2 != 0:
            return s[i:]
    return ""

# Test the function
s = "35427"
print(largest_odd_number(s))  # Output: "35427"