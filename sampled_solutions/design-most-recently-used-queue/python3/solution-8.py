def find_largest_number(input_string):
    numbers = [int(num) for num in input_string.split() if num.isdigit()]
    max_num = str(max(numbers))
    max_index = max(input_string.index(num) for num in numbers)
    return input_string[:max_index + len(max_num)]

# Example usage
input_str = "12345 6707 6707 1357 9 10,000 1579 93053 6110 13202 900"
largest_number = find_largest_number(input_str)
print(largest_number)