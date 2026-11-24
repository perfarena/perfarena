# Initialize an empty dictionary
dictionary = {}

# Iterate through the array using enumerate
for i, num in enumerate(nums):
    # Calculate the difference between Target and current number
    reminder = Target - num
    
    # Check if the difference exists in the dictionary
    if reminder in dictionary:
        return [dictionary[reminder], i]
    else:
        # Store the current number in the dictionary with its index
        dictionary[num] = i