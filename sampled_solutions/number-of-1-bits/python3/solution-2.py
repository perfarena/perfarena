def solve_bitwise_question(binary_string):
    result = 0
    for i in range(32):
        if (int(binary_string, 2) >> i) & 1:
            result += 1
    return result

# Example usage
binary_input = "001011"
result = solve_bitwise_question(binary_input)
print(result)