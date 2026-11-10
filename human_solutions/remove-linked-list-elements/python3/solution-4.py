# Define the function to remove elements from the linked list
def remove_elements(head, val):
    # Check if the head is null
    if not head:
        return head

    # Create a temporary node to iterate through the list
    temp = head

    # Iterate through the linked list
    while temp and temp.next:
        # Check if the next node's value matches the value to be removed
        if temp.next.val == val:
            # Skip the node by adjusting the pointers
            temp.next = temp.next.next
        else:
            # Move to the next node
            temp = temp.next

    # Return the updated linked list
    return head

# Time Complexity: O(n) - n is the number of nodes in the linked list
# Space Complexity: O(1) - constant space is used