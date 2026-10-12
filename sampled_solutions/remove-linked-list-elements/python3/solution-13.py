def scale_values(values, argument):
    for i in range(len(values)):
        values[i] *= argument
    return values

# Example usage
original_values = [1, 2, 6, 3, 5, 3, 4, 5, 6]
argument = 6
scaled_values = scale_values(original_values, argument)
print(scaled_values)