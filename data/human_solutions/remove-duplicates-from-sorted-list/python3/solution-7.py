# Update answer variable with head pointer
answer = head

# Loop through the list
while head and head.next:
    # Check if current value is equal to next value
    if head.val == head.next.val:
        # Update next node to skip duplicate
        head.next = head.next.next
    else:
        # Move head pointer to next node
        head = head.next

# Return the answer variable
return answer