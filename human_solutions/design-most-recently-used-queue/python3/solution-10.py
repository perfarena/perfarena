def find_largest_odd_number(input_str):
    for i in range(len(input_str) - 1, -1, -1):
        num = int(input_str[i])
        if num % 2 == 1:
            return input_str[:i+1]
    return ""

# Example Usage
input_string = "123456789"
result = find_largest_odd_number(input_string)
print(result)  # Output: "9"