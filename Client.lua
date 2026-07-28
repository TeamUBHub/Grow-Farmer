if game.GameId ~= 10200395747 then return end
local HttpService = game:GetService("HttpService")
local TweenService = game:GetService("TweenService")
local Players = game:GetService("Players")
local GuiService = game:GetService("GuiService")

local WS_URL = "ws://127.0.0.1:8765"
local WebSocketConnection = nil

local function SendMessage(MessageData)
    if not WebSocketConnection then
        return false, nil
    end
    
    local Success, Error = pcall(function()
        local JSON = HttpService:JSONEncode(MessageData)
        WebSocketConnection:Send(JSON)
    end)
    
    if not Success then
        warn("Failed to send message:", Error)
        return false, nil
    end
    return true, nil
end

local function EstablishConnection()
    local Success, Connection = pcall(function()
        return WebSocket.connect(WS_URL)
    end)
    
    if Success and Connection then
        WebSocketConnection = Connection
        
        Connection.OnMessage:Connect(function(Message)
            print("Received:", Message)
        end)
        
        Connection.OnClose:Connect(function()
            WebSocketConnection = nil
        end)
        
        return true
    else
        warn("Failed to connect:", Connection)
        return false
    end
end

local function Terminate()
    return SendMessage({action = "kill"})
end

EstablishConnection()

local function GetRootPart()
    local LocalPlayer = Players.LocalPlayer
    local Character = LocalPlayer.Character
    if not Character or not Character.Parent then
        Character = LocalPlayer.CharacterAdded:Wait()
    end
    local RootPart = Character:WaitForChild("HumanoidRootPart", 5)
    if not RootPart then
        Character = LocalPlayer.CharacterAdded:Wait()
        RootPart = Character:WaitForChild("HumanoidRootPart")
    end
    
    return RootPart
end

local function MoveToPosition(TargetCFrame)
    local RootPart = GetRootPart()
    if not RootPart then return end
    local Distance = (RootPart.Position - TargetCFrame.Position).Magnitude
    local TweenInfoData = TweenInfo.new(Distance / 35, Enum.EasingStyle.Linear)
    local Tween = TweenService:Create(RootPart, TweenInfoData, {CFrame = TargetCFrame})
    Tween:Play()
    Tween.Completed:Wait()
end

local function SendTamePacket(TargetPart)
    local RemoteEvent = game.ReplicatedStorage:WaitForChild("SharedModules"):WaitForChild("Packet"):WaitForChild("RemoteEvent")
    local PacketID = RemoteEvent:GetAttribute("WildPetTame") or 77
    local TameBuffer = buffer.create(2)
    buffer.writeu16(TameBuffer, 0, PacketID)
    RemoteEvent:FireServer(TameBuffer, {[1] = TargetPart})
end

local function HandlePet(PetObject)
    local PrimaryPart = PetObject:IsA("Model") and (PetObject.PrimaryPart or PetObject:FindFirstChildWhichIsA("BasePart")) or PetObject
    if PrimaryPart then
        MoveToPosition(PrimaryPart.CFrame * CFrame.new(0, 3, 0))
        SendTamePacket(PetObject)
        task.wait(1)
    end
end

GuiService.ErrorMessageChanged:Connect(function()
    local ErrorType = GuiService:GetErrorType()
    if ErrorType == Enum.ConnectionError.DisconnectErrors then
        task.wait(1)
        Terminate()
    end
end)
task.wait(30)
local WildPetReference = workspace.Map:WaitForChild("WildPetRef")
repeat task.wait(0.5) until #WildPetReference:GetChildren() > 0
local Pets = WildPetReference:GetChildren()
local RemainingPets = #Pets
for _, Pet in ipairs(Pets) do
    HandlePet(Pet)
    RemainingPets -= 1
end
repeat task.wait(0.1) until RemainingPets <= 0

Terminate()
