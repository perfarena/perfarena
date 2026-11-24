def equal_frequency(freq1, freq2):
    for i in range(256):
        if freq1[i] < freq2[i]:
            return False
    return True

def min_window(s, t):
    if len(t) > len(s):
        return ""

    freq_t = [0] * 256
    for char in t:
        freq_t[ord(char)] += 1

    left, right = 0, 0
    min_window_size = float('inf')
    answer = ""
    freq_window = [0] * 256

    while right < len(s):
        freq_window[ord(s[right])] += 1
        right += 1

        while equal_frequency(freq_window, freq_t):
            if right - left < min_window_size:
                min_window_size = right - left
                answer = s[left:right]

            freq_window[ord(s[left])] -= 1
            left += 1

    return answer