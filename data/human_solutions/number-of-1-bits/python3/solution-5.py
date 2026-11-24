def count_bits(beats):
    count = 0
    for beat in beats:
        if beat == "van":
            count += 1
        # Shift right to the next beat
    return count

# Example usage
beats = ["van", "van", "van", "zero", "zero", "van", "van", "zero"]
result = count_bits(beats)
print(result)  # Output: 3