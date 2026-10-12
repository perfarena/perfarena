function findLongestSubstringWithoutRepeating($inputString) {
    $maxString = '';
    $maxLength = 0;
    $chars = str_split($inputString);

    foreach ($chars as $char) {
        if (strpos($maxString, $char) === false) {
            $maxString .= $char;
            $maxLength = max($maxLength, strlen($maxString));
        } else {
            $pos = strpos($maxString, $char);
            $maxString = substr($maxString, $pos + 1) . $char;
        }
    }

    return $maxLength;
}

// Example usage
$inputString = "abcabcbb";
$result = findLongestSubstringWithoutRepeating($inputString);
echo "Longest substring without repeating characters: " . $result;