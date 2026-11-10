def convert_to_zigzag(s, num_rows):
    if num_rows == 1 or len(s) <= num_rows:
        return s

    rows = ['' for _ in range(num_rows)]
    current_row, direction = 0, -1
    for char in s:
        rows[current_row] += char
        if current_row == 0 or current_row == num_rows - 1:
            direction *= -1
        current_row += direction

    return ''.join(rows)

# Example usage
input_string = "pishing"
num_rows = 3
result = convert_to_zigzag(input_string, num_rows)
print(result)  # Output: "phsiing"