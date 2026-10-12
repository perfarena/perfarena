def sorted_squares(nums):
    negatives = [num ** 2 for num in nums if num < 0]
    positives = [num ** 2 for num in nums if num >= 0]

    negatives.sort(reverse=True)
    positives.sort()

    result = []
    i, j = 0, 0

    while i < len(negatives) and j < len(positives):
        if negatives[i] < positives[j]:
            result.append(negatives[i])
            i += 1
        else:
            result.append(positives[j])
            j += 1

    result.extend(negatives[i:])
    result.extend(positives[j])

    return result