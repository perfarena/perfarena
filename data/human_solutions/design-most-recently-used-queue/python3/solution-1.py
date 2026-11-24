def largest_odd_number(s: str) -> str:
    for i in range(len(s) - 1, -1, -1):
        if int(s[i]) % 2 == 1:
            return s[:i+1]
    return ""

# Test the function with examples
print(largest_odd_number("52"))  # Output: "5"
print(largest_odd_number("2468"))  # Output: ""
print(largest_odd_number("1234567"))  # Output: "1234567"