if game.GameId ~= 10200395747 then return end
local HttpService = game:GetService("HttpService")
local TweenService = game:GetService("TweenService")
local Players = game:GetService("Players")
local LocalPlayer = Players.LocalPlayer

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
task.wait(10)

local function MoveToPosition(TargetCFrame)
    local Character = LocalPlayer.CharacterAdded:Wait()
    local RootPart = Character:WaitForChild("HumanoidRootPart")
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
        task.wait(0.1)
        SendTamePacket(PetObject)
    end
end

local WildPetReference = workspace.Map:WaitForChild("WildPetRef")
if WildPetReference then
    for _, Pet in ipairs(WildPetReference:GetChildren()) do
        HandlePet(Pet)
    end
    Terminate()
end
