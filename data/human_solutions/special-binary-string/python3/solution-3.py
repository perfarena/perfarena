# Create dictionary to store last index of each character
d = {char: idx for idx, char in enumerate(s)}

# Initialize left and right pointers
left = right = 0
results = []

# Iterate through the string using two-pointer method
for idx, char in enumerate(s):
    right = max(right, d[char])
    if idx == right:
        results.append(right - left + 1)
        left = idx + 1

return results