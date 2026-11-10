def make_unique_string(input_string):
    # Step 1: Calculate character frequencies
    frequency = {}
    for char in input_string:
        if char in frequency:
            frequency[char] += 1
        else:
            frequency[char] = 1

    # Step 2: Adjust frequencies to make them unique
    adjustments = 0
    for char in frequency:
        while frequency[char] in frequency.values():
            frequency[char] += 1
            adjustments += 1

    return adjustments

# Step 4: Test the function with an example input
input_str = "ABCD"
result = make_unique_string(input_str)
print(result)  # Output should be the total number of adjustments made