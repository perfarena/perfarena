def length_of_longest_substring(s):
    count = [0] * 256
    l = r = 0
    answer = 0

    while r < len(s):
        count[ord(s[r])] += 1

        while count[ord(s[r])] > 1:
            count[ord(s[l])] -= 1
            l += 1

        answer = max(answer, r - l + 1)
        r += 1

    return answer